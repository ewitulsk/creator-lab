"""The in-app "api" runner: claims Gemini jobs and runs them inside the server process.

These jobs need no GPU or media tools beyond ffmpeg, and the key already lives in
backend/.env, so there is no separate runner to start. It registers like any runner (named
api@<host>), heartbeats while it works and reports progress the same way, so the Jobs
page shows e.g. "Gemini levers: video 3 of 12, run 2 of 3".
"""

from __future__ import annotations

import logging
import socket
import threading

from flask import Flask

from .. import jobs as jobsvc
from ..extensions import db
from ..models import Job, Runner, Video, utcnow
from . import config as lcfg
from . import gemini, rubric, service

log = logging.getLogger(__name__)
CAPABILITIES = [service.ANALYZER, service.PAIRWISE]
_thread: threading.Thread | None = None
_stop = threading.Event()


def runner_row() -> Runner:
    name = f"api@{socket.gethostname()}"
    r = db.session.scalar(db.select(Runner).where(Runner.name == name))
    if r is None:
        r = Runner(name=name)
        db.session.add(r)
    r.type, r.capabilities, r.host, r.version = "api", CAPABILITIES, socket.gethostname(), "levers-1"
    r.last_heartbeat = utcnow()
    db.session.commit()
    return r


def process_next_job(sleep=None) -> Job | None:
    """Claim and run one Gemini job, if any is ready. Returns it (or None)."""
    if not lcfg.key_is_set():
        return None
    runner = runner_row()
    job = jobsvc.claim(runner, CAPABILITIES)
    if job is None:
        return None
    run_job(job, sleep=sleep)
    return job


def run_job(job: Job, sleep=None) -> None:
    cfg = {**lcfg.get_config(), **((job.params or {}).get("config") or {})}
    try:
        r = rubric.load()
        client = gemini.make_client(lcfg.api_key())
    except Exception as err:
        jobsvc.fail(job, service.scrub(f"{type(err).__name__}: {err}"))
        return
    title = "Gemini levers" if job.job_type == service.ANALYZER else "Gemini pairwise"
    state = {"video_index": 0, "video_id": None, "n": len(job.items)}

    def progress(msg: str) -> None:
        jobsvc.report_progress(
            job,
            {
                "stage": title,
                "stage_index": 1,
                "stage_count": 1,
                "video_index": state["video_index"],
                "video_count": state["n"],
                "video_id": state["video_id"],
                "message": msg,
                "overall_percent": 100 * max(state["video_index"] - 1, 0) / max(state["n"], 1),
            },
        )

    def cancelled() -> bool:
        db.session.refresh(job)
        return job.status == "cancelled"

    ctx = service.Ctx(job=job, client=client, cfg=cfg, rubric=r, progress=progress, cancelled=cancelled)
    if sleep is not None:
        ctx.sleep = sleep
    try:
        if job.job_type == service.ANALYZER:
            _run_scores(job, ctx, state)
        else:
            _run_pairs(job, ctx, state)
        if job.status in ("claimed", "running"):
            jobsvc.complete(job)
            cost = service.job_cost(job.id)
            job.progress = {
                **(job.progress or {}),
                "message": f"finished; {cost['calls']} calls, ${cost['cost_usd']:.2f}",
            }
            db.session.commit()
    except Exception as err:
        db.session.rollback()
        log.exception("Gemini job #%s failed", job.id)
        if job.status in ("claimed", "running"):
            jobsvc.fail(job, service.scrub(f"{type(err).__name__}: {err}") or "failed")


def _run_scores(job: Job, ctx: service.Ctx, state: dict) -> None:
    items = sorted(job.items, key=lambda i: i.id)
    done = service.already_scored([i.video_id for i in items], ctx.cfg, ctx.rubric)
    ab = bool((job.params or {}).get("ab"))
    for n, item in enumerate(items, 1):
        if item.status == "done":
            continue
        if ctx.cancelled():
            return
        state["video_index"], state["video_id"] = n, item.video_id
        video = db.session.get(Video, item.video_id)
        try:
            if ab:
                summary = {}
                for mode in ("upload", "youtube_url"):
                    ctx.cfg = {**ctx.cfg, "input_mode": mode}
                    ctx.progress(f"A/B: {mode}")
                    summary[mode] = service.score_video(ctx, video)["scores"]
                result = {"status": "done", "summary": {"ab": summary}}
            elif item.video_id in done:
                result = {"status": "done", "summary": {"skipped": "already scored with this model and rubric"}}
            else:
                result = {"status": "done", "summary": service.score_video(ctx, video)}
        except Exception as err:
            db.session.rollback()
            if str(err) == "cancelled":
                return
            log.warning("levers failed for video %s: %s", item.video_id, service.scrub(str(err)))
            result = {"status": "failed", "error": service.scrub(f"{type(err).__name__}: {err}")[:1000]}
        jobsvc.report_item_result(job, item.video_id, result)


def _run_pairs(job: Job, ctx: service.Ctx, state: dict) -> None:
    params = job.params or {}
    pairs = params.get("pairs") or []
    remaining: dict[int, int] = {}
    for p in pairs:
        for vid in (p["a"], p["b"]):
            remaining[vid] = remaining.get(vid, 0) + 1
    failed: dict[int, str] = {}
    state["n"] = len(pairs)
    for n, p in enumerate(pairs, 1):
        if ctx.cancelled():
            return
        state["video_index"], state["video_id"] = n, p["a"]
        a, b = db.session.get(Video, p["a"]), db.session.get(Video, p["b"])
        try:
            service.judge_pair(ctx, a, b, study_id=params.get("study_id"), pair_key=p.get("pair_key"))
        except Exception as err:
            db.session.rollback()
            if str(err) == "cancelled":
                return
            msg = service.scrub(f"{type(err).__name__}: {err}")[:1000]
            failed[p["a"]] = failed[p["b"]] = msg
        for vid in (p["a"], p["b"]):
            remaining[vid] -= 1
            if remaining[vid] == 0:
                status = "failed" if vid in failed else "done"
                data = {"status": status, "error": failed.get(vid)} if status == "failed" else {"status": "done"}
                jobsvc.report_item_result(job, vid, data)


def start(app: Flask) -> None:
    """Poll for Gemini jobs on a daemon thread (serve only, never in tests)."""
    global _thread
    if _thread is not None:
        return

    def loop():
        while not _stop.is_set():
            ran = None
            try:
                with app.app_context():
                    ran = process_next_job()
            except Exception:
                log.exception("Gemini worker error")
            finally:
                with app.app_context():
                    db.session.remove()
            _stop.wait(1 if ran else 5)

    _thread = threading.Thread(target=loop, name="levers-worker", daemon=True)
    _thread.start()
    log.info("Gemini lever worker started (key %s)", "set" if lcfg.key_is_set() else "not set")
