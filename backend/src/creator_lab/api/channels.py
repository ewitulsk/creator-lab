"""Channels: add, list, edit, refresh, delete; plus their videos and history."""

from __future__ import annotations

import shutil

from flask import request
from sqlalchemy import func

from .. import background, ingest
from ..extensions import db
from ..models import Asset, Channel, ChannelSnapshot, Video
from ..serializers import channel_detail, channel_snapshot, channel_summary
from ..storage import data_root
from ..youtube.client import ChannelNotFound, YouTubeError
from ..youtube.quota import QuotaExceeded
from . import ApiError, body, bp
from .videos import video_list_response


def youtube_call(fn, *args, **kwargs):
    """Map YouTube failures to API errors."""
    try:
        return fn(*args, **kwargs)
    except QuotaExceeded as err:
        raise ApiError(str(err), 429) from err
    except ChannelNotFound as err:
        raise ApiError(str(err), 404) from err
    except ValueError as err:
        raise ApiError(str(err), 400) from err
    except YouTubeError as err:
        raise ApiError(str(err), 502) from err


def channel_stats(ids: list[int] | None = None) -> dict[int, dict]:
    """Latest snapshot, video counts by format and selection, and disk use, per channel."""
    latest = (
        db.select(ChannelSnapshot.channel_id, func.max(ChannelSnapshot.captured_at).label("at"))
        .group_by(ChannelSnapshot.channel_id)
        .subquery()
    )
    q = db.select(ChannelSnapshot).join(
        latest,
        (ChannelSnapshot.channel_id == latest.c.channel_id) & (ChannelSnapshot.captured_at == latest.c.at),
    )
    if ids is not None:
        q = q.where(ChannelSnapshot.channel_id.in_(ids))
    out: dict[int, dict] = {}
    for s in db.session.scalars(q):
        out.setdefault(s.channel_id, {}).update(subscribers=s.subscribers, total_views=s.views, uploads=s.video_count)
    fq = db.select(Video.channel_id, Video.format, Video.selection, func.count()).where(Video.gone.is_(False))
    if ids is not None:
        fq = fq.where(Video.channel_id.in_(ids))
    for cid, fmt, sel, n in db.session.execute(fq.group_by(Video.channel_id, Video.format, Video.selection)):
        d = out.setdefault(cid, {})
        d.setdefault("videos_by_format", {})
        d["videos_by_format"][fmt] = d["videos_by_format"].get(fmt, 0) + n
        d["video_count"] = d.get("video_count", 0) + n
        if sel == "tracked":
            d["tracked_count"] = d.get("tracked_count", 0) + n
    dq = db.select(Video.channel_id, func.coalesce(func.sum(Asset.bytes), 0)).join(Asset)
    if ids is not None:
        dq = dq.where(Video.channel_id.in_(ids))
    for cid, n in db.session.execute(dq.group_by(Video.channel_id)):
        out.setdefault(cid, {})["disk_bytes"] = int(n)
    for cid in ids or out.keys():
        out.setdefault(cid, {})["syncing"] = background.is_running(f"list:{cid}")
    return out


def get_channel(channel_id: int) -> Channel:
    ch = db.session.get(Channel, channel_id)
    if ch is None:
        raise ApiError("channel not found", 404)
    return ch


def start_listing(ch: Channel) -> bool:
    return background.submit(f"list:{ch.id}", ingest.list_uploads, ch.id)


@bp.get("/channels")
def list_channels():
    q = db.select(Channel).order_by(Channel.is_mine.desc(), Channel.followed.desc(), Channel.title)
    if request.args.get("followed") == "1":
        q = q.where(Channel.followed)
    chans = db.session.scalars(q).all()
    stats = channel_stats()
    return {"channels": [channel_summary(c, stats.get(c.id)) for c in chans]}


@bp.post("/channels")
def create_channel():
    """Add a channel by URL, @handle or id and list its uploads in the background.
    Listing stores metadata only ('discovered'); nothing is downloaded."""
    data = body()
    text = str(data.get("channel", "")).strip()
    if not text:
        raise ApiError("channel is required (URL, @handle or UC... id)")
    ch, created = youtube_call(
        ingest.add_channel,
        text,
        follow=bool(data["follow"]) if "follow" in data else None,
        is_mine=bool(data["is_mine"]) if "is_mine" in data else None,
        tags=[str(t) for t in data.get("tags") or []],
    )
    if data.get("list_uploads", True) and not ingest.listing_is_fresh(ch):
        start_listing(ch)
    return {"channel": channel_summary(ch, channel_stats([ch.id]).get(ch.id)), "created": created}, (
        201 if created else 200
    )


@bp.get("/channels/<int:channel_id>")
def get_channel_detail(channel_id: int):
    ch = get_channel(channel_id)
    return {"channel": channel_detail(ch, channel_stats([ch.id]).get(ch.id, {}))}


@bp.patch("/channels/<int:channel_id>")
def update_channel(channel_id: int):
    ch = get_channel(channel_id)
    data = body()
    for key in ("followed", "auto_track_new", "is_mine"):
        if key in data:
            setattr(ch, key, bool(data[key]))
    if "tags" in data:
        if not isinstance(data["tags"], list):
            raise ApiError("tags must be a list")
        ch.tags = sorted({str(t).strip() for t in data["tags"] if str(t).strip()})
    if "notes" in data:
        ch.notes = str(data["notes"] or "") or None
    db.session.commit()
    return {"channel": channel_detail(ch, channel_stats([ch.id]).get(ch.id, {}))}


@bp.delete("/channels/<int:channel_id>")
def delete_channel(channel_id: int):
    ch = get_channel(channel_id)
    folder = data_root() / "media" / ch.external_id
    db.session.delete(ch)
    db.session.commit()
    if request.args.get("delete_files") == "1" and folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True}


@bp.post("/channels/<int:channel_id>/refresh")
def refresh_channel(channel_id: int):
    """Re-list all uploads (refreshes discovered videos' stats). ~2 units per 50 videos."""
    ch = get_channel(channel_id)
    if not request.args.get("force") and ingest.listing_is_fresh(ch):
        return {"started": False, "already_running": False, "fresh": True}
    started = start_listing(ch)
    return {"started": started, "already_running": not started, "fresh": False}


@bp.get("/channels/<int:channel_id>/snapshots")
def channel_snapshots(channel_id: int):
    ch = get_channel(channel_id)
    return {"snapshots": [channel_snapshot(s) for s in ch.snapshots]}


@bp.get("/channels/<int:channel_id>/videos")
def channel_videos(channel_id: int):
    get_channel(channel_id)
    return video_list_response(channel_id=channel_id)
