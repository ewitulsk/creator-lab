"""Model → JSON shapes shared by the API modules (they match openapi.yaml)."""

from __future__ import annotations

from .models import Asset, Channel, ChannelSnapshot, Video, VideoSnapshot, iso


def channel_summary(ch: Channel, stats: dict | None = None) -> dict:
    stats = stats or {}
    return {
        "id": ch.id,
        "external_id": ch.external_id,
        "handle": ch.handle,
        "title": ch.title,
        "thumbnail_url": ch.thumbnail_url,
        "country": ch.country,
        "followed": ch.followed,
        "auto_track_new": ch.auto_track_new,
        "is_mine": ch.is_mine,
        "tags": ch.tags or [],
        "added_at": iso(ch.added_at),
        "backfilled_at": iso(ch.backfilled_at),
        "last_synced_at": iso(ch.last_synced_at),
        "sync_error": ch.sync_error,
        "syncing": stats.get("syncing", False),
        "subscribers": stats.get("subscribers"),
        "total_views": stats.get("total_views"),
        "uploads": stats.get("uploads"),
        "video_count": stats.get("video_count"),
        "tracked_count": stats.get("tracked_count", 0),
        "videos_by_format": stats.get("videos_by_format", {}),
        "disk_bytes": stats.get("disk_bytes", 0),
    }


def channel_detail(ch: Channel, stats: dict) -> dict:
    return channel_summary(ch, stats) | {
        "description": ch.description,
        "notes": ch.notes,
        "uploads_playlist": ch.uploads_playlist,
        "published_at": iso(ch.published_at),
        "url": f"https://www.youtube.com/channel/{ch.external_id}",
    }


def channel_snapshot(s: ChannelSnapshot) -> dict:
    return {
        "captured_at": iso(s.captured_at),
        "subscribers": s.subscribers,
        "views": s.views,
        "video_count": s.video_count,
    }


def video_summary(v: Video, has_thumbnail: bool | None = None) -> dict:
    return {
        "id": v.id,
        "external_id": v.external_id,
        "channel_id": v.channel_id,
        "title": v.title,
        "published_at": iso(v.published_at),
        "duration_s": v.duration_s,
        "format": v.format,
        "aspect": v.aspect,
        "views": v.views,
        "likes": v.likes,
        "comments": v.comments,
        "outlier_score": v.outlier_score,
        "outlier_baseline": v.outlier_baseline,
        "thumbnail": f"/api/videos/{v.id}/thumbnail",
        "has_thumbnail": has_thumbnail,
        "gone": v.gone,
        "selection": v.selection,
        "tracked_at": iso(v.tracked_at),
        "url": v.url,
    }


def video_snapshot(s: VideoSnapshot) -> dict:
    return {
        "captured_at": iso(s.captured_at),
        "views": s.views,
        "likes": s.likes,
        "comments": s.comments,
        "source": s.source,
    }


def asset(a: Asset) -> dict:
    return {
        "id": a.id,
        "kind": a.kind,
        "variant": a.variant,
        "path": a.path,
        "bytes": a.bytes,
        "width": a.width,
        "height": a.height,
        "codec": a.codec,
        "url": f"/api/assets/{a.id}/file",
        "created_at": iso(a.created_at),
    }
