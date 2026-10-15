"""Health, OpenAPI spec, dashboard and settings."""

from __future__ import annotations

import datetime as dt
from importlib import resources

import yaml
from flask import current_app, request
from sqlalchemy import func

from ..auth import public
from ..config import is_set
from ..extensions import db
from ..models import (
    AnalysisResult,
    ApiUsage,
    Asset,
    Channel,
    ChannelSnapshot,
    Job,
    Runner,
    Study,
    Video,
    VideoSnapshot,
    iso,
    utcnow,
)
from ..settings import all_settings, get_setting, update_settings
from ..storage import disk_report
from . import ApiError, body, bp

_spec_cache: dict | None = None


def openapi_spec() -> dict:
    global _spec_cache
    if _spec_cache is None:
        text = resources.files("creator_lab").joinpath("openapi.yaml").read_text(encoding="utf-8")
        _spec_cache = yaml.safe_load(text)
    return _spec_cache


@bp.get("/health")
@public
def health():
    return {"ok": True, "time": iso(utcnow())}


@bp.get("/openapi.json")
@public
def openapi():
    return openapi_spec()


def runner_online(r: Runner) -> bool:
    if r.last_heartbeat is None:
        return False
    timeout = dt.timedelta(minutes=get_setting("runner_timeout_minutes"))
    return utcnow() - r.last_heartbeat < timeout


def quota_today() -> dict:
    # YouTube quota resets at midnight Pacific time.
    from ..youtube.quota import pacific_today

    row = db.session.get(ApiUsage, (pacific_today(), "youtube_data"))
    return {
        "day": pacific_today().isoformat(),
        "units": row.units if row else 0,
        "calls": row.calls if row else 0,
        "budget": get_setting("daily_quota_budget"),
    }


@bp.get("/dashboard")
def dashboard():
    cfg = current_app.config["APP"]
    count = lambda model: db.session.scalar(db.select(func.count()).select_from(model))  # noqa: E731
    jobs_by_status = dict(db.session.execute(db.select(Job.status, func.count()).group_by(Job.status)).all())
    videos_by_format = dict(db.session.execute(db.select(Video.format, func.count()).group_by(Video.format)).all())
    runners = db.session.scalars(db.select(Runner).order_by(Runner.name)).all()
    failed = db.session.scalars(
        db.select(Job).where(Job.status == "failed").order_by(Job.finished_at.desc()).limit(5)
    ).all()
    channel_errors = db.session.scalars(db.select(Channel).where(Channel.sync_error.is_not(None)).limit(5)).all()
    return {
        "counts": {
            "channels": count(Channel),
            "videos": count(Video),
            "video_snapshots": count(VideoSnapshot),
            "channel_snapshots": count(ChannelSnapshot),
            "assets": count(Asset),
            "results_done": db.session.scalar(
                db.select(func.count()).select_from(AnalysisResult).where(AnalysisResult.status == "done")
            ),
            "studies": count(Study),
        },
        "videos_by_format": videos_by_format,
        "jobs_by_status": jobs_by_status,
        "runners": [
            {
                "id": r.id,
                "name": r.name,
                "type": r.type,
                "online": runner_online(r),
                "last_heartbeat": iso(r.last_heartbeat),
            }
            for r in runners
        ],
        "errors": [
            {
                "kind": "job",
                "id": j.id,
                "title": f"{j.job_type} job #{j.id} failed",
                "detail": (j.error or "")[:300],
                "at": iso(j.finished_at),
            }
            for j in failed
        ]
        + [
            {
                "kind": "channel",
                "id": c.id,
                "title": f"Sync failed for {c.title or c.handle}",
                "detail": (c.sync_error or "")[:300],
                "at": iso(c.last_synced_at),
            }
            for c in channel_errors
        ],
        "disk": disk_report(cfg.data_root, refresh=request.args.get("refresh") == "1"),
        "quota": quota_today(),
    }


@bp.get("/settings")
def get_settings():
    cfg = current_app.config["APP"]
    return {
        "keys": {
            "youtube_api_key": is_set(cfg.youtube_api_key),
            "runner_token": is_set(cfg.runner_token),
            "secret_key": is_set(cfg.secret_key),
        },
        "data_root": str(cfg.data_root),
        "brainthing_dir": str(cfg.brainthing_dir) if cfg.brainthing_dir else None,
        "max_video_height": cfg.max_video_height,
        "schedules": all_settings(),
        "disk": disk_report(cfg.data_root),
        "quota": quota_today(),
    }


@bp.put("/settings/schedules")
def put_schedules():
    try:
        return {"schedules": update_settings(body())}
    except ValueError as err:
        raise ApiError(str(err)) from err
