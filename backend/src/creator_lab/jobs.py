"""The job queue: the platform issues jobs, runners pull and run them.

A job is one analyzer run over one or more videos (job items). Runners claim
jobs of the types they advertise, report live progress, post a result per video
and finally complete or fail the job. The platform never runs heavy models.

In v1 runners live on the same machine, so job payloads carry local file paths.
Remote runners would get signed URLs instead (see `input_ref`).
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from flask import current_app

from .analyzers import current
from .extensions import db
from .models import (
    ACTIVE_JOB_STATUSES,
    AnalysisResult,
    Analyzer,
    Asset,
    Job,
    JobItem,
    Runner,
    Video,
    iso,
    utcnow,
)
from .settings import get_setting
from .storage import absolute, analyzer_dir, data_root, logs_dir, media_dir, rel

log = logging.getLogger(__name__)

TERMINAL = ("done", "failed", "cancelled")
# Asset kind each analyzer input name refers to.
INPUT_ASSET = {"media": "video", "title_text": "title_text", "thumbnail": "thumbnail", "captions": "captions_text"}


class JobError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ------------------------------------------------------------------ results bookkeeping


def result_row(video_id: int, analyzer: Analyzer) -> AnalysisResult:
    row = db.session.scalar(
        db.select(AnalysisResult).where(AnalysisResult.video_id == video_id, AnalysisResult.analyzer_id == analyzer.id)
    )
    if row is None:
        row = AnalysisResult(video_id=video_id, analyzer_id=analyzer.id, status="missing", summary={})
        db.session.add(row)
    return row


def has_asset(video_id: int, kind: str) -> bool:
    return (
        db.session.scalar(db.select(Asset.id).where(Asset.video_id == video_id, Asset.kind == kind).limit(1))
        is not None
    )


def active_item_video_ids(analyzer: Analyzer) -> set[int]:
    q = (
        db.select(JobItem.video_id)
        .join(Job)
        .where(Job.analyzer_id == analyzer.id, Job.status.in_(ACTIVE_JOB_STATUSES))
        .where(JobItem.status.in_(("queued", "running")))
    )
    return set(db.session.scalars(q))


# ------------------------------------------------------------------ issuing


def issue_job(
    analyzer_name: str,
    video_ids: list[int],
    *,
    params: dict | None = None,
    created_by: str = "system",
    priority: int = 0,
    force: bool = False,
) -> Job | None:
    """Queue one job for every video that still needs this analyzer.

    Skips videos the analyzer does not apply to, videos already in an active job of
    this analyzer and (unless force) videos already done. If the analyzer needs the
    downloaded video, a download job is issued first and this job waits for it.
    """
    analyzer = current(analyzer_name)
    if analyzer.runner_type == "builtin":
        raise JobError(f"{analyzer_name} is computed in-app; it has no jobs")
    if analyzer.runner_type == "api":
        from .levers import config as lcfg

        if not lcfg.key_is_set():
            raise JobError("GEMINI_API_KEY is not set in backend/.env, so the Gemini lever analyzers are unavailable")
        # Freeze the settings for this job so a change mid-batch can't mix configurations.
        params = {**(params or {}), "config": {**lcfg.get_config(), **((params or {}).get("config") or {})}}
    videos = list(db.session.scalars(db.select(Video).where(Video.id.in_(video_ids), Video.gone.is_(False))))
    if analyzer.applies_to:
        videos = [v for v in videos if v.format in analyzer.applies_to]
    busy = active_item_video_ids(analyzer)
    videos = [v for v in videos if v.id not in busy]
    if not force:
        done = set(
            db.session.scalars(
                db.select(AnalysisResult.video_id).where(
                    AnalysisResult.analyzer_id == analyzer.id,
                    AnalysisResult.status == "done",
                    AnalysisResult.video_id.in_([v.id for v in videos]),
                )
            )
        )
        if analyzer.name == "levers_gemini":
            # Done means scored at this analyzer version + rubric hash + model + input mode,
            # not just "has a result": a new rubric or model scores again.
            from .levers import rubric as lrubric
            from .levers import service as lsvc

            done = lsvc.already_scored([v.id for v in videos], params["config"], lrubric.load())
        if analyzer.name == "media":  # a file already on disk counts as done
            for v in videos:
                if v.id not in done and has_asset(v.id, "video"):
                    row = result_row(v.id, analyzer)
                    row.status, row.computed_at = "done", utcnow()
                    done.add(v.id)
        videos = [v for v in videos if v.id not in done]
    if not videos:
        db.session.commit()
        return None

    depends_on: list[int] = []
    for name in analyzer.inputs or []:
        depends_on += _ensure_input(name, videos, created_by, priority)
    depends_on = sorted(set(depends_on))
    job = Job(
        analyzer_id=analyzer.id,
        job_type=analyzer.name,
        runner_type=analyzer.runner_type,
        status="queued",
        priority=priority,
        params=params or {},
        depends_on=depends_on,
        created_by=created_by,
        progress={},
        stage_timings=[],
    )
    db.session.add(job)
    db.session.flush()
    job.log_path = rel(logs_dir() / "jobs" / f"{job.id}.log")
    for v in sorted(videos, key=lambda v: v.published_at or dt.datetime.min):
        db.session.add(JobItem(job_id=job.id, video_id=v.id, status="queued"))
        row = result_row(v.id, analyzer)
        row.status, row.job_id, row.error = "queued", job.id, None
    db.session.commit()
    log.info("issued %s job #%d for %d videos", analyzer.name, job.id, len(videos))
    return job


def _done_video_ids(analyzer: Analyzer, video_ids: list[int]) -> set[int]:
    return set(
        db.session.scalars(
            db.select(AnalysisResult.video_id).where(
                AnalysisResult.analyzer_id == analyzer.id,
                AnalysisResult.status == "done",
                AnalysisResult.video_id.in_(video_ids),
            )
        )
    )


def _active_jobs_for(analyzer: Analyzer, video_ids: list[int]) -> list[int]:
    return list(
        db.session.scalars(
            db.select(Job.id)
            .join(JobItem)
            .where(Job.analyzer_id == analyzer.id, Job.status.in_(ACTIVE_JOB_STATUSES), JobItem.video_id.in_(video_ids))
            .distinct()
        )
    )


def _ensure_input(name: str, videos: list[Video], created_by: str, priority: int) -> list[int]:
    """Make sure an input exists or is on its way. Returns the job ids to wait for.

    media / analyzer outputs: issue the producing job for videos missing it (and wait for
    jobs already producing it). thumbnail: fetched in-app right now (small).
    """
    if name == "thumbnail":
        from .ingest import fetch_thumbnail

        for v in videos:
            if not has_asset(v.id, "thumbnail"):
                try:
                    fetch_thumbnail(v)
                except Exception as err:  # the job reports the missing input per video
                    log.warning("thumbnail fetch failed for %s: %s", v.external_id, err)
        db.session.commit()
        return []
    if name == "title_text" or name == "captions":
        return []
    try:
        producer = current(name)
    except KeyError:
        return []
    if producer.runner_type == "builtin":
        return []
    ids = [v.id for v in videos]
    if name == "media":
        missing = [v.id for v in videos if not has_asset(v.id, "video")]
    else:
        done = _done_video_ids(producer, ids)
        missing = [i for i in ids if i not in done]
    if not missing:
        return []
    waits = []
    job = issue_job(name, missing, created_by=created_by, priority=priority + 1)
    if job is not None:
        waits.append(job.id)
    waits += [j for j in _active_jobs_for(producer, missing) if j not in waits]
    return waits


def queue_media_jobs(video_ids: list[int], created_by: str = "system") -> list[Job]:
    """Download and captions jobs for freshly pulled videos (1080p cap)."""
    jobs = [issue_job(name, video_ids, created_by=created_by) for name in ("media", "captions")]
    return [j for j in jobs if j is not None]


# ------------------------------------------------------------------ runners


def register_runner(data: dict) -> Runner:
    name = str(data.get("name") or "").strip()
    if not name:
        raise JobError("runner name is required")
    rtype = data.get("type")
    if rtype not in ("cpu", "gpu"):
        raise JobError("runner type must be cpu or gpu")
    caps = data.get("capabilities")
    if not isinstance(caps, list) or not all(isinstance(c, str) for c in caps):
        raise JobError("capabilities must be a list of job types")
    r = db.session.scalar(db.select(Runner).where(Runner.name == name))
    if r is None:
        r = Runner(name=name)
        db.session.add(r)
    r.type, r.capabilities = rtype, caps
    r.gpu, r.host, r.version = data.get("gpu"), data.get("host"), data.get("version")
    r.info = data.get("info") or {}
    r.last_heartbeat = utcnow()
    db.session.commit()
    # A runner that restarts mid-job has lost that job: put it back in the queue.
    for job in db.session.scalars(db.select(Job).where(Job.claimed_by == r.id, Job.status.in_(("claimed", "running")))):
        requeue(job, "runner restarted")
    db.session.commit()
    return r


def heartbeat(runner: Runner, info: dict | None) -> None:
    runner.last_heartbeat = utcnow()
    if info:
        runner.info = {**(runner.info or {}), **info}
    db.session.commit()


def deps_finished(job: Job) -> bool:
    if not job.depends_on:
        return True
    statuses = db.session.scalars(db.select(Job.status).where(Job.id.in_(job.depends_on)))
    return all(s in TERMINAL for s in statuses)


def claim(runner: Runner, types: list[str] | None = None) -> Job | None:
    """Atomically hand the next ready job of an advertised type to this runner."""
    types = [t for t in (types or runner.capabilities) if t in (runner.capabilities or [])]
    if not types:
        return None
    candidates = db.session.scalars(
        db.select(Job)
        .where(Job.status == "queued", Job.job_type.in_(types))
        .order_by(Job.priority.desc(), Job.id)
        .limit(50)
    ).all()
    now = utcnow()
    for job in candidates:
        if not deps_finished(job):
            continue
        # Compare-and-set: only one runner can move a job out of 'queued'.
        updated = db.session.execute(
            db.update(Job)
            .where(Job.id == job.id, Job.status == "queued")
            .values(status="claimed", claimed_by=runner.id, claimed_at=now, attempts=Job.attempts + 1, updated_at=now)
        ).rowcount
        db.session.commit()
        if updated == 1:
            db.session.refresh(job)
            return job
    return None


def requeue(job: Job, reason: str) -> None:
    if job.attempts >= job.max_attempts:
        finish_failed(job, f"{reason}; gave up after {job.attempts} attempts")
        return
    job.status, job.claimed_by, job.claimed_at = "queued", None, None
    job.progress = {"message": f"requeued: {reason}"}
    for item in job.items:
        if item.status == "running":
            item.status = "queued"
    append_log(job, f"[platform] requeued: {reason}")


def requeue_stale_jobs() -> int:
    """Jobs whose runner has been silent past the timeout go back in the queue."""
    cutoff = utcnow() - dt.timedelta(minutes=get_setting("runner_timeout_minutes"))
    stale = db.session.scalars(
        db.select(Job)
        .join(Runner, Job.claimed_by == Runner.id)
        .where(Job.status.in_(("claimed", "running")), Runner.last_heartbeat < cutoff)
    ).all()
    for job in stale:
        requeue(job, "runner stopped sending heartbeats")
    db.session.commit()
    return len(stale)


# ------------------------------------------------------------------ job payload for runners


def input_ref(path: Path | None) -> str | None:
    """Where a runner finds an input. Local path in v1; the seam for signed URLs later."""
    return str(path) if path is not None else None


def item_payload(job: Job, item: JobItem) -> dict:
    v = item.video
    ch = v.channel
    assets: dict[str, Asset] = {}
    for a in sorted(v.assets, key=lambda a: a.variant != ""):  # unvariant (default) copy first
        assets.setdefault(a.kind, a)
    inputs = {}
    for name in [*(job.analyzer.inputs or []), "title_text", "thumbnail", "media", "captions"]:
        kind = INPUT_ASSET.get(name)
        if kind:
            a = assets.get(kind)
            inputs[name] = input_ref(absolute(a.path)) if a else None
            continue
        # Another analyzer's output folder (e.g. tribe_video for tribe_engagement).
        try:
            producer = current(name)
        except KeyError:
            inputs[name] = None
            continue
        row = db.session.scalar(
            db.select(AnalysisResult).where(
                AnalysisResult.video_id == v.id,
                AnalysisResult.analyzer_id == producer.id,
                AnalysisResult.status == "done",
            )
        )
        inputs[name] = input_ref(absolute(row.output_path)) if row and row.output_path else None
    if job.job_type in ("media", "captions", "heatmap"):
        out = media_dir(ch.external_id, v.external_id)
    else:
        out = analyzer_dir(job.analyzer.name, job.analyzer.version, v.external_id)
    return {
        "video_id": v.id,
        "external_id": v.external_id,
        "channel_external_id": ch.external_id,
        "title": v.title,
        "url": v.url,
        "duration_s": v.duration_s,
        "format": v.format,
        "language": v.language,
        "inputs": inputs,
        "output_dir": str(out),
        "status": item.status,
    }


def job_payload(job: Job) -> dict:
    cfg = current_app.config["APP"]
    return {
        "id": job.id,
        "type": job.job_type,
        "analyzer": job.analyzer.name,
        "version": job.analyzer.version,
        "status": job.status,
        "params": job.params or {},
        "attempt": job.attempts,
        "max_video_height": cfg.max_video_height,
        "data_root": str(data_root()),
        "runner_cache_dir": str(data_root() / "runner_cache"),
        "download_pause_seconds": get_setting("download_pause_seconds"),
        "items": [item_payload(job, i) for i in job.items],
    }


# ------------------------------------------------------------------ progress and results


def append_log(job: Job, text: str) -> None:
    if not text:
        return
    path = absolute(job.log_path) if job.log_path else logs_dir() / "jobs" / f"{job.id}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")


def read_log(job: Job, tail_lines: int = 300) -> str:
    if not job.log_path:
        return ""
    path = absolute(job.log_path)
    if not path.exists():
        return ""
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 256 * 1024))
        data = f.read().decode("utf-8", errors="replace")
    return "\n".join(data.splitlines()[-tail_lines:])


PROGRESS_FIELDS = {
    "stage": str,
    "stage_index": int,
    "stage_count": int,
    "video_index": int,
    "video_count": int,
    "video_id": int,
    "stage_percent": float,
    "overall_percent": float,
    "message": str,
    "eta_seconds": float,
}


def clean_progress(data: dict) -> dict:
    out = {}
    for key, cast in PROGRESS_FIELDS.items():
        if data.get(key) is not None:
            try:
                out[key] = cast(data[key])
            except (TypeError, ValueError) as err:
                raise JobError(f"progress field {key} must be {cast.__name__}") from err
    for key in ("stage_percent", "overall_percent"):
        if key in out:
            out[key] = max(0.0, min(100.0, round(out[key], 2)))
    return out


def report_progress(job: Job, data: dict) -> dict:
    """Store the runner's exact live state. Returns {'cancel': bool}."""
    if job.status == "cancelled":
        return {"cancel": True}
    if job.status not in ("claimed", "running"):
        raise JobError(f"job is {job.status}")
    now = utcnow()
    progress = clean_progress(data)
    progress["updated_at"] = iso(now)
    if job.status == "claimed":
        job.status, job.started_at = "running", now
    # Per-stage timings: a new stage name closes the previous one.
    timings = list(job.stage_timings or [])
    stage = progress.get("stage")
    if stage and (not timings or timings[-1]["stage"] != stage or timings[-1].get("finished_at")):
        if timings and not timings[-1].get("finished_at"):
            _close_stage(timings[-1], now)
        timings.append({"stage": stage, "started_at": iso(now), "finished_at": None, "seconds": None})
    job.stage_timings = timings
    job.progress = progress
    vid = progress.get("video_id")
    if vid:
        for item in job.items:
            if item.video_id == vid and item.status in ("queued", "running"):
                if item.status == "queued":
                    item.status, item.started_at = "running", now
                    result_row(item.video_id, job.analyzer).status = "running"
                item.stage = stage
    if data.get("log"):
        append_log(job, str(data["log"]))
    if job.claimed_by:
        r = db.session.get(Runner, job.claimed_by)
        if r:
            r.last_heartbeat = now
    db.session.commit()
    return {"cancel": False}


def _close_stage(entry: dict, now: dt.datetime) -> None:
    entry["finished_at"] = iso(now)
    started = dt.datetime.fromisoformat(entry["started_at"].rstrip("Z"))
    entry["seconds"] = round((now - started).total_seconds(), 1)


ALLOWED_ASSET_KINDS = {"video", "audio", "thumbnail", "captions", "captions_text", "info"}


def report_item_result(job: Job, video_id: int, data: dict) -> None:
    item = next((i for i in job.items if i.video_id == video_id), None)
    if item is None:
        raise JobError("video is not part of this job", 404)
    if job.status not in ("claimed", "running"):
        raise JobError(f"job is {job.status}")
    status = data.get("status", "done")
    if status not in ("done", "failed"):
        raise JobError("status must be done or failed")
    now = utcnow()
    item.status, item.finished_at = status, now
    item.error = data.get("error")
    row = result_row(video_id, job.analyzer)
    row.status, row.job_id, row.computed_at = status, job.id, now
    row.error = data.get("error")
    if status == "done":
        row.summary = data.get("summary") or {}
        if data.get("output_path"):
            row.output_path = rel(Path(data["output_path"]))
        for a in data.get("assets") or []:
            register_asset(item.video, a)
        if isinstance(data.get("capture"), dict):
            from .captures import store_capture

            source = "heatmap" if job.job_type == "heatmap" else "download"
            cap = store_capture(item.video, data["capture"], job.id, source)
            if job.job_type != "heatmap":  # a download's metadata includes the heatmap too
                hm = result_row(video_id, current("heatmap"))
                hm.status, hm.computed_at, hm.job_id = "done", now, job.id
                hm.summary = {"points": cap.heatmap_points, "chapters": cap.chapter_count, "source": "download"}
        updates = data.get("video_updates") or {}
        if updates.get("format") in ("live", "premiere"):
            item.video.format = updates["format"]
    db.session.commit()


def register_asset(video: Video, a: dict) -> Asset:
    kind = a.get("kind")
    if kind not in ALLOWED_ASSET_KINDS:
        raise JobError(f"asset kind must be one of {sorted(ALLOWED_ASSET_KINDS)}")
    path = Path(a["path"])
    try:
        relpath = rel(path)
    except ValueError as err:
        raise JobError(f"asset path must be inside the data folder: {path}") from err
    variant = str(a.get("variant") or "")
    asset = db.session.scalar(
        db.select(Asset).where(Asset.video_id == video.id, Asset.kind == kind, Asset.variant == variant)
    )
    if asset is None:
        asset = Asset(video_id=video.id, kind=kind, variant=variant, path=relpath)
        db.session.add(asset)
    asset.path = relpath
    asset.bytes = a.get("bytes") or (path.stat().st_size if path.exists() else None)
    for key in ("sha256", "width", "height", "codec"):
        if a.get(key) is not None:
            setattr(asset, key, a[key])
    asset.meta = a.get("meta") or {}
    return asset


def _finish_items(job: Job, reason: str) -> None:
    for item in job.items:
        if item.status in ("queued", "running"):
            item.status, item.error, item.finished_at = "failed", reason, utcnow()
            row = result_row(item.video_id, job.analyzer)
            row.status, row.error = "failed", reason


def complete(job: Job) -> None:
    if job.status not in ("claimed", "running"):
        raise JobError(f"job is {job.status}")
    now = utcnow()
    _finish_items(job, "the runner did not report a result for this video")
    timings = list(job.stage_timings or [])
    if timings and not timings[-1].get("finished_at"):
        _close_stage(timings[-1], now)
    job.stage_timings = timings
    failed = sum(1 for i in job.items if i.status == "failed")
    job.status = "done" if failed < len(job.items) or not job.items else "failed"
    if failed:
        job.error = f"{failed} of {len(job.items)} videos failed"
    job.finished_at = now
    job.progress = {**(job.progress or {}), "overall_percent": 100.0, "message": "finished"}
    db.session.commit()


def finish_failed(job: Job, error: str, log_tail: str | None = None) -> None:
    now = utcnow()
    job.status, job.error, job.finished_at = "failed", error[:4000], now
    if log_tail:
        job.log_tail = log_tail[-20000:]
    timings = list(job.stage_timings or [])
    if timings and not timings[-1].get("finished_at"):
        _close_stage(timings[-1], now)
    job.stage_timings = timings
    _finish_items(job, error[:500])


def fail(job: Job, error: str, log_tail: str | None = None, retry: bool = False) -> None:
    if job.status not in ("claimed", "running"):
        raise JobError(f"job is {job.status}")
    if log_tail:
        append_log(job, "[runner] failure log tail:\n" + log_tail[-20000:])
    if retry and job.attempts < job.max_attempts:
        job.log_tail = (log_tail or "")[-20000:] or None
        requeue(job, error[:300])
    else:
        finish_failed(job, error, log_tail)
    db.session.commit()


def cancel(job: Job) -> None:
    if job.status in TERMINAL:
        raise JobError(f"job is already {job.status}")
    now = utcnow()
    job.status, job.finished_at = "cancelled", now
    for item in job.items:
        if item.status in ("queued", "running"):
            item.status, item.finished_at = "cancelled", now
            row = result_row(item.video_id, job.analyzer)
            if row.job_id == job.id and row.status in ("queued", "running"):
                row.status = "missing"
    # Jobs waiting on this one can't run as planned; cancel them too.
    for dep in db.session.scalars(db.select(Job).where(Job.status == "queued")):
        if job.id in (dep.depends_on or []):
            cancel(dep)
    db.session.commit()


def retry(job: Job, created_by: str) -> Job | None:
    """A fresh job for this job's videos that did not finish."""
    if job.status in ACTIVE_JOB_STATUSES:
        raise JobError("job is still active")
    ids = [i.video_id for i in job.items if i.status != "done"]
    if not ids:
        raise JobError("every video in this job finished")
    return issue_job(job.job_type, ids, params=job.params, created_by=created_by, priority=job.priority, force=True)
