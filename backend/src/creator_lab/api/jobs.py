"""Jobs and runners.

Runner calls (Bearer RUNNER_TOKEN): register, heartbeat, claim, progress, item results,
complete, fail, log. Runners identify themselves on job calls with `X-Runner-Id`; a call
for a job that has since been requeued to someone else gets 409, so the runner stops.

User calls: issue jobs, list/inspect them, cancel, retry, tail logs, watch live (SSE).
"""

from __future__ import annotations

import datetime as dt
import json
import time

from flask import Response, request, stream_with_context
from sqlalchemy import func

from .. import jobs as svc
from ..auth import actor, runner_only, runner_or_user
from ..extensions import db
from ..models import ACTIVE_JOB_STATUSES, Analyzer, Job, JobItem, Runner, Video, iso, utcnow
from . import ApiError, body, bp


def call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except svc.JobError as err:
        raise ApiError(str(err), err.status) from err
    except KeyError as err:
        raise ApiError(str(err).strip("'\""), 400) from err


def get_job(job_id: int) -> Job:
    job = db.session.get(Job, job_id)
    if job is None:
        raise ApiError("job not found", 404)
    return job


def runner_job(job_id: int) -> Job:
    """The job, checked to still belong to the calling runner."""
    job = get_job(job_id)
    rid = request.headers.get("X-Runner-Id")
    if rid and job.claimed_by is not None and str(job.claimed_by) != rid and job.status != "cancelled":
        raise ApiError("this job is no longer yours (it was requeued)", 409)
    return job


def runner_summary(r: Runner, active: dict[int, Job] | None = None) -> dict:
    from .system import runner_online

    job = (active or {}).get(r.id)
    return {
        "id": r.id,
        "name": r.name,
        "type": r.type,
        "gpu": r.gpu,
        "host": r.host,
        "version": r.version,
        "capabilities": r.capabilities or [],
        "info": r.info or {},
        "online": runner_online(r),
        "last_heartbeat": iso(r.last_heartbeat),
        "current_job_id": job.id if job else None,
    }


def progress_text(job: Job) -> str:
    """'Whisper on video 5 of 10, 1%'."""
    p = job.progress or {}
    if job.status == "queued":
        if job.depends_on and not svc.deps_finished(job):
            return "waiting for " + ", ".join(f"#{d}" for d in job.depends_on)
        return p.get("message") or "queued"
    if job.status == "claimed":
        return "claimed, starting"
    if job.status != "running":
        return p.get("message") or job.status
    parts = []
    if p.get("stage"):
        parts.append(p["stage"])
    if p.get("video_index") and p.get("video_count"):
        parts.append(f"on video {p['video_index']} of {p['video_count']}")
    text = " ".join(parts)
    if p.get("stage_percent") is not None:
        text += f", {p['stage_percent']:.0f}%"
    return text or p.get("message") or "running"


def job_summary(job: Job) -> dict:
    counts: dict[str, int] = {}
    for item in job.items:
        counts[item.status] = counts.get(item.status, 0) + 1
    started = job.started_at or job.claimed_at
    end = job.finished_at or (utcnow() if job.status in ACTIVE_JOB_STATUSES else None)
    return {
        "id": job.id,
        "type": job.job_type,
        "analyzer": job.analyzer.name,
        "version": job.analyzer.version,
        "title": job.analyzer.title,
        "runner_type": job.runner_type,
        "status": job.status,
        "priority": job.priority,
        "created_by": job.created_by,
        "runner": {"id": job.runner.id, "name": job.runner.name} if job.runner else None,
        "progress": job.progress or {},
        "progress_text": progress_text(job),
        "items_total": len(job.items),
        "items_by_status": counts,
        "attempts": job.attempts,
        "depends_on": job.depends_on or [],
        "error": job.error,
        "created_at": iso(job.created_at),
        "claimed_at": iso(job.claimed_at),
        "started_at": iso(job.started_at),
        "finished_at": iso(job.finished_at),
        "elapsed_seconds": round((end - started).total_seconds(), 1) if started and end else None,
    }


def job_detail(job: Job) -> dict:
    return job_summary(job) | {
        "params": job.params or {},
        "stage_timings": job.stage_timings or [],
        "log_tail": job.log_tail,
        "items": [
            {
                "video_id": i.video_id,
                "title": i.video.title,
                "external_id": i.video.external_id,
                "status": i.status,
                "stage": i.stage,
                "error": i.error,
                "started_at": iso(i.started_at),
                "finished_at": iso(i.finished_at),
            }
            for i in job.items
        ],
        "payload": svc.job_payload(job),
    }


# ------------------------------------------------------------------ runner endpoints


@bp.post("/runners/register")
@runner_only
def register_runner():
    r = call(svc.register_runner, body())
    return {"runner": runner_summary(r)}


@bp.post("/runners/<int:runner_id>/heartbeat")
@runner_only
def runner_heartbeat(runner_id: int):
    r = db.session.get(Runner, runner_id)
    if r is None:
        raise ApiError("unknown runner; register again", 404)
    data = request.get_json(silent=True) or {}
    svc.heartbeat(r, data.get("info"))
    cancelled = list(
        db.session.scalars(
            db.select(Job.id).where(
                Job.claimed_by == r.id, Job.status == "cancelled", Job.finished_at > utcnow() - dt.timedelta(hours=1)
            )
        )
    )
    return {"ok": True, "cancelled_jobs": cancelled}


@bp.post("/jobs/claim")
@runner_only
def claim_job():
    data = body()
    r = db.session.get(Runner, int(data.get("runner_id", 0)))
    if r is None:
        raise ApiError("unknown runner; register again", 404)
    svc.heartbeat(r, None)
    job = svc.claim(r, data.get("types"))
    if job is None:
        return {"job": None}
    return {"job": svc.job_payload(job)}


@bp.post("/jobs/<int:job_id>/progress")
@runner_only
def job_progress(job_id: int):
    return call(svc.report_progress, runner_job(job_id), body())


@bp.post("/jobs/<int:job_id>/items/<int:video_id>/result")
@runner_only
def job_item_result(job_id: int, video_id: int):
    call(svc.report_item_result, runner_job(job_id), video_id, body())
    return {"ok": True}


@bp.post("/jobs/<int:job_id>/complete")
@runner_only
def job_complete(job_id: int):
    job = runner_job(job_id)
    call(svc.complete, job)
    return {"job": job_summary(job)}


@bp.post("/jobs/<int:job_id>/fail")
@runner_only
def job_fail(job_id: int):
    job = runner_job(job_id)
    data = body()
    call(svc.fail, job, str(data.get("error") or "failed"), data.get("log_tail"), bool(data.get("retry", False)))
    return {"job": job_summary(job)}


@bp.post("/jobs/<int:job_id>/log")
@runner_only
def job_log_append(job_id: int):
    job = runner_job(job_id)
    svc.append_log(job, str(body().get("text", "")))
    return {"ok": True}


# ------------------------------------------------------------------ user endpoints


@bp.get("/jobs/<int:job_id>")
@runner_or_user
def job_get(job_id: int):
    return {"job": job_detail(get_job(job_id))}


@bp.get("/jobs/<int:job_id>/log")
def job_log(job_id: int):
    job = get_job(job_id)
    tail = min(int(request.args.get("tail", 300)), 5000)
    return {"log": svc.read_log(job, tail)}


@bp.get("/jobs")
def list_jobs():
    q = db.select(Job)
    if request.args.get("status"):
        q = q.where(Job.status.in_(request.args["status"].split(",")))
    if request.args.get("type"):
        q = q.where(Job.job_type.in_(request.args["type"].split(",")))
    if request.args.get("video_id"):
        q = q.join(JobItem).where(JobItem.video_id == int(request.args["video_id"]))
    total = db.session.scalar(db.select(func.count()).select_from(q.subquery()))
    limit = min(int(request.args.get("limit", 100)), 500)
    jobs = db.session.scalars(q.order_by(Job.id.desc()).limit(limit).offset(int(request.args.get("offset", 0))))
    return {"jobs": [job_summary(j) for j in jobs], "total": total}


@bp.post("/jobs")
def create_jobs():
    """Issue jobs: {analyzer, video_ids} or {requests: [{analyzer, video_ids}, ...]}."""
    data = body()
    requests_ = data.get("requests") or [{"analyzer": data.get("analyzer"), "video_ids": data.get("video_ids")}]
    issued, skipped = [], []
    for req in requests_:
        name, ids = req.get("analyzer"), req.get("video_ids")
        if not isinstance(name, str) or not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            raise ApiError("each request needs an analyzer name and a list of video_ids")
        job = call(
            svc.issue_job,
            name,
            ids,
            params=req.get("params") or data.get("params"),
            created_by=actor(),
            priority=int(data.get("priority", 0)),
            force=bool(data.get("force", False)),
        )
        if job is None:
            skipped.append(name)
        else:
            issued.append(job)
    # The job just issued may have chained a download first: report both.
    all_ids = {j.id for j in issued} | {d for j in issued for d in (j.depends_on or [])}
    jobs = [db.session.get(Job, i) for i in sorted(all_ids)]
    return {"jobs": [job_summary(j) for j in jobs], "skipped": skipped}


@bp.post("/jobs/<int:job_id>/cancel")
def job_cancel(job_id: int):
    job = get_job(job_id)
    call(svc.cancel, job)
    return {"job": job_summary(job)}


@bp.post("/jobs/<int:job_id>/retry")
def job_retry(job_id: int):
    job = call(svc.retry, get_job(job_id), actor())
    if job is None:
        raise ApiError("nothing to retry", 400)
    return {"job": job_summary(job)}


@bp.get("/runners")
def list_runners():
    active = {
        j.claimed_by: j
        for j in db.session.scalars(db.select(Job).where(Job.status.in_(("claimed", "running"))))
        if j.claimed_by
    }
    runners = db.session.scalars(db.select(Runner).order_by(Runner.name))
    return {"runners": [runner_summary(r, active) for r in runners]}


@bp.get("/analyzers")
def list_analyzers():
    rows = db.session.scalars(db.select(Analyzer).order_by(Analyzer.id)).all()
    return {
        "analyzers": [
            {
                "name": a.name,
                "version": a.version,
                "ref": a.ref,
                "title": a.title,
                "description": a.description,
                "runner_type": a.runner_type,
                "inputs": a.inputs or [],
                "output_format": a.output_format,
                "applies_to": a.applies_to or [],
                "is_current": a.is_current,
            }
            for a in rows
        ]
    }


def stream_snapshot() -> dict:
    recent = utcnow() - dt.timedelta(minutes=10)
    jobs = db.session.scalars(
        db.select(Job)
        .where(db.or_(Job.status.in_(ACTIVE_JOB_STATUSES), Job.finished_at >= recent))
        .order_by(Job.id.desc())
        .limit(100)
    ).all()
    from .system import runner_online

    runners = db.session.scalars(db.select(Runner)).all()
    return {
        "jobs": [job_summary(j) for j in jobs],
        "runners_online": sum(1 for r in runners if runner_online(r)),
    }


@bp.get("/jobs/stream")
def jobs_stream():
    """Server-sent events: the active jobs (and ones finished in the last 10 minutes),
    pushed whenever they change. Clients fall back to polling /api/jobs."""
    max_seconds = 300 if not request.args.get("once") else 0

    @stream_with_context
    def gen():
        last, started, last_ping = None, time.monotonic(), 0.0
        yield "retry: 3000\n\n"
        while True:
            snap = stream_snapshot()
            snap.pop("now", None)
            text = json.dumps(snap, separators=(",", ":"), default=str)
            db.session.remove()
            if text != last:
                last = text
                yield f"event: jobs\ndata: {text}\n\n"
            elif time.monotonic() - last_ping > 15:
                last_ping = time.monotonic()
                yield ": ping\n\n"
            if time.monotonic() - started >= max_seconds:
                return
            time.sleep(1.5)

    return Response(
        gen(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


# ------------------------------------------------------------------ coverage


@bp.get("/coverage")
def coverage():
    """Videos x analyzers status grid."""
    args = request.args
    q = db.select(Video).where(Video.gone.is_(False))
    if args.get("channel_id"):
        q = q.where(Video.channel_id.in_([int(x) for x in args["channel_id"].split(",") if x.isdigit()]))
    selection = args.get("selection", "tracked")
    if selection in ("tracked", "discovered"):
        q = q.where(Video.selection == selection)
    if args.get("format"):
        q = q.where(Video.format.in_(args["format"].split(",")))
    if args.get("video_ids"):
        q = q.where(Video.id.in_([int(x) for x in args["video_ids"].split(",") if x.isdigit()]))
    total = db.session.scalar(db.select(func.count()).select_from(q.subquery()))
    limit = min(int(args.get("limit", 200)), 2000)
    videos = list(
        db.session.scalars(q.order_by(Video.published_at.desc()).limit(limit).offset(int(args.get("offset", 0))))
    )
    analyzers = db.session.scalars(db.select(Analyzer).where(Analyzer.is_current).order_by(Analyzer.id)).all()
    from ..models import AnalysisResult

    rows = db.session.execute(
        db.select(
            AnalysisResult.video_id, AnalysisResult.analyzer_id, AnalysisResult.status, AnalysisResult.job_id
        ).where(AnalysisResult.video_id.in_([v.id for v in videos]))
    )
    cells: dict[int, dict[str, dict]] = {}
    by_id = {a.id: a for a in analyzers}
    for vid, aid, status, job_id in rows:
        if aid in by_id:
            cells.setdefault(vid, {})[by_id[aid].name] = {"status": status, "job_id": job_id}
    out = []
    for v in videos:
        row = {}
        for a in analyzers:
            if a.applies_to and v.format not in a.applies_to:
                row[a.name] = {"status": "n/a", "job_id": None}
            else:
                row[a.name] = cells.get(v.id, {}).get(a.name, {"status": "missing", "job_id": None})
        out.append(
            {
                "video_id": v.id,
                "title": v.title,
                "channel_id": v.channel_id,
                "format": v.format,
                "published_at": iso(v.published_at),
                "selection": v.selection,
                "cells": row,
            }
        )
    return {
        "analyzers": [
            {"name": a.name, "version": a.version, "title": a.title, "runner_type": a.runner_type} for a in analyzers
        ],
        "videos": out,
        "total": total,
    }
