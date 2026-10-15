"""In-process background scheduler (APScheduler)."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None

# (job id, function, minutes between runs). Functions take the Flask app.
TASKS: list[tuple[str, object, int]] = []


def task(job_id: str, minutes: int):
    def deco(fn):
        TASKS.append((job_id, fn, minutes))
        return fn

    return deco


def _wrap(app: Flask, fn):
    def run():
        with app.app_context():
            try:
                fn(app)
            except Exception:
                log.exception("scheduled task %s failed", getattr(fn, "__name__", fn))
            finally:
                from .extensions import db

                db.session.remove()

    return run


def start_scheduler(app: Flask) -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = BackgroundScheduler(daemon=True, job_defaults={"coalesce": True, "max_instances": 1})
    for job_id, fn, minutes in TASKS:
        _scheduler.add_job(_wrap(app, fn), "interval", minutes=minutes, id=job_id, next_run_time=None)
        # First run shortly after start, not immediately, so startup stays fast.
        _scheduler.modify_job(job_id, next_run_time=_soon(30))
    _scheduler.start()
    log.info("Scheduler started with %d tasks", len(TASKS))
    return _scheduler


def _soon(seconds: int):
    import datetime as dt

    return dt.datetime.now().astimezone() + dt.timedelta(seconds=seconds)
