"""Side-by-side channel statistics for the Compare page."""

from __future__ import annotations

import datetime as dt
import statistics

from .extensions import db
from .models import Channel, ChannelSnapshot, Video, iso


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def channel_profile(ch: Channel, months: int = 12) -> dict:
    videos = list(db.session.scalars(db.select(Video).where(Video.channel_id == ch.id, Video.gone.is_(False))))
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    since = now - dt.timedelta(days=30 * months)
    out: dict = {
        "id": ch.id,
        "title": ch.title,
        "handle": ch.handle,
        "thumbnail_url": ch.thumbnail_url,
        "owned": ch.owned,
    }
    for fmt in ("long", "short"):
        vs = [v for v in videos if v.format == fmt]
        recent = [v for v in vs if v.published_at and v.published_at >= since]
        views = [v.views for v in recent if v.views is not None]
        scores = [v.outlier_score for v in recent if v.outlier_score is not None]
        likes = [v.likes / v.views for v in recent if v.likes and v.views]
        comments = [v.comments / v.views for v in recent if v.comments and v.views]
        out[fmt] = {
            "count": len(vs),
            "recent_count": len(recent),
            "uploads_per_month": round(len(recent) / months, 2),
            "median_views": _median(views),
            "median_duration_s": _median([v.duration_s for v in recent if v.duration_s]),
            "hit_rate": round(sum(1 for s in scores if s >= 2) / len(scores), 4) if scores else None,
            "flop_rate": round(sum(1 for s in scores if s <= 0.5) / len(scores), 4) if scores else None,
            "like_rate": round(_median(likes), 5) if likes else None,
            "comment_rate": round(_median(comments), 6) if comments else None,
        }
    total = len([v for v in videos if v.format in ("long", "short")])
    out["shorts_share"] = round(out["short"]["count"] / total, 4) if total else None
    # Monthly uploads and median views (all time, for the charts).
    monthly: dict[str, dict] = {}
    for v in videos:
        if not v.published_at or v.format not in ("long", "short"):
            continue
        key = v.published_at.strftime("%Y-%m")
        m = monthly.setdefault(key, {"month": key, "long": 0, "short": 0, "views": []})
        m[v.format] += 1
        if v.views is not None and v.format == "long":
            m["views"].append(v.views)
    out["monthly"] = [
        {"month": k, "long": m["long"], "short": m["short"], "median_long_views": _median(m["views"])}
        for k, m in sorted(monthly.items())
    ]
    out["subscribers"] = [
        {"captured_at": iso(s.captured_at), "subscribers": s.subscribers}
        for s in db.session.scalars(
            db.select(ChannelSnapshot).where(ChannelSnapshot.channel_id == ch.id).order_by(ChannelSnapshot.captured_at)
        )
        if s.subscribers is not None
    ]
    # Each upload relative to its channel baseline, for a normalized scatter.
    out["videos"] = [
        {
            "id": v.id,
            "title": v.title,
            "published_at": iso(v.published_at),
            "format": v.format,
            "views": v.views,
            "outlier_score": v.outlier_score,
        }
        for v in videos
        if v.published_at and v.format in ("long", "short") and v.outlier_score is not None
    ]
    return out
