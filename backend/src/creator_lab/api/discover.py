"""Discover: find a channel, browse and filter all its uploads, pull the ones to analyze.

Browsing stores metadata only (videos stay 'discovered'). Pulling marks videos
'tracked' and queues their media jobs.
"""

from __future__ import annotations

import datetime as dt

from flask import request

from .. import ingest
from ..extensions import db
from ..models import Asset, Video, iso, utcnow
from ..serializers import video_summary
from . import ApiError, body, bp
from .channels import get_channel, youtube_call

TIMEFRAMES = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}


def _date(text: str | None) -> dt.datetime | None:
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError as err:
        raise ApiError(f"bad date {text!r}; use YYYY-MM-DD") from err


@bp.get("/discover/search")
def discover_search():
    q = request.args.get("q", "").strip()
    if not q:
        raise ApiError("q is required")
    limit = min(int(request.args.get("limit", 10)), 25)
    return {"channels": youtube_call(ingest.search_channels, q, limit)}


def filter_channel_videos(channel_id: int, args) -> tuple[list[Video], int]:
    """Apply Discover's filters. Returns (picked videos, number matching before top/bottom N)."""
    q = db.select(Video).where(Video.channel_id == channel_id, Video.gone.is_(False))
    now = utcnow()
    timeframe = args.get("timeframe", "all")
    if timeframe in TIMEFRAMES:
        q = q.where(Video.published_at >= now - dt.timedelta(days=TIMEFRAMES[timeframe]))
    elif timeframe == "custom":
        since, until = _date(args.get("since")), _date(args.get("until"))
        if since:
            q = q.where(Video.published_at >= since)
        if until:
            q = q.where(Video.published_at < until + dt.timedelta(days=1))
    elif timeframe != "all":
        raise ApiError("timeframe must be 7d, 30d, 90d, 1y, all or custom")
    formats = [f for f in args.get("formats", "long,short").split(",") if f]
    if args.get("include_live") == "1":
        formats += ["live", "premiere"]
    q = q.where(Video.format.in_(formats))
    if args.get("min_duration"):
        q = q.where(Video.duration_s >= int(args["min_duration"]))
    if args.get("selection") in ("discovered", "tracked"):
        q = q.where(Video.selection == args["selection"])
    videos = list(db.session.scalars(q))
    total = len(videos)

    rank = args.get("rank", "outlier")
    if rank not in ("outlier", "views"):
        raise ApiError("rank must be outlier or views")
    pick = args.get("pick", "all")
    n = int(args.get("n", 10))

    def key(v: Video):
        return v.outlier_score if rank == "outlier" else v.views

    if pick in ("top", "bottom"):
        ranked = [v for v in videos if key(v) is not None]
        ranked.sort(key=key, reverse=pick == "top")
        videos = ranked[:n]
    elif pick == "all":
        videos.sort(key=lambda v: v.published_at or dt.datetime.min, reverse=True)
    else:
        raise ApiError("pick must be top, bottom or all")
    return videos, total


@bp.get("/discover/channels/<int:channel_id>/videos")
def discover_channel_videos(channel_id: int):
    ch = get_channel(channel_id)
    videos, total = filter_channel_videos(channel_id, request.args)
    thumbs = set(
        db.session.scalars(
            db.select(Asset.video_id).where(Asset.kind == "thumbnail", Asset.video_id.in_([v.id for v in videos]))
        )
    )
    from .channels import channel_stats

    return {
        "videos": [video_summary(v, v.id in thumbs) | {"role": None, "group": None} for v in videos],
        "matching": total,
        "listing": {
            "listed_at": iso(ch.backfilled_at),
            "fresh": ingest.listing_is_fresh(ch),
            "syncing": channel_stats([ch.id])[ch.id]["syncing"],
            "error": ch.sync_error,
        },
    }


@bp.post("/discover/pull")
def discover_pull():
    data = body()
    ids = data.get("video_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
        raise ApiError("video_ids must be a non-empty list of ids")
    if len(ids) > 2000:
        raise ApiError("pull at most 2000 videos at a time")
    found = db.session.scalar(db.select(db.func.count()).select_from(Video).where(Video.id.in_(ids)))
    if found != len(set(ids)):
        raise ApiError("some videos do not exist", 404)
    from ..auth import actor

    result = youtube_call(ingest.pull_videos, ids, created_by=actor())
    return result
