"""Store yt-dlp info captures: 'Most replayed' heatmap, chapters and a stats snapshot."""

from __future__ import annotations

import datetime as dt

from .extensions import db
from .models import Video, VideoChapter, VideoHeatmapPoint, VideoInfoCapture, utcnow


def store_capture(video: Video, capture: dict, job_id: int | None, source: str) -> VideoInfoCapture:
    try:
        when = dt.datetime.fromisoformat(str(capture.get("captured_at", "")).rstrip("Z"))
    except ValueError:
        when = utcnow()
    heatmap = [p for p in capture.get("heatmap") or [] if _point_ok(p)]
    chapters = [c for c in capture.get("chapters") or [] if isinstance(c, dict) and c.get("start_s") is not None]
    snapshot = capture.get("snapshot") if isinstance(capture.get("snapshot"), dict) else {}
    cap = VideoInfoCapture(
        video_id=video.id,
        job_id=job_id,
        source=source,
        captured_at=when,
        snapshot=snapshot,
        heatmap_points=len(heatmap),
        chapter_count=len(chapters),
    )
    db.session.add(cap)
    db.session.flush()
    for p in heatmap:
        db.session.add(
            VideoHeatmapPoint(
                video_id=video.id,
                capture_id=cap.id,
                start_s=float(p["start_s"]),
                end_s=float(p["end_s"]),
                value=float(p["value"]),
                captured_at=when,
            )
        )
    for c in chapters:
        db.session.add(
            VideoChapter(
                video_id=video.id,
                capture_id=cap.id,
                start_s=float(c["start_s"]),
                end_s=float(c["end_s"]) if c.get("end_s") is not None else None,
                title=str(c.get("title") or "")[:500],
            )
        )
    return cap


def _point_ok(p) -> bool:
    try:
        return isinstance(p, dict) and float(p["end_s"]) >= float(p["start_s"]) and p.get("value") is not None
    except (KeyError, TypeError, ValueError):
        return False
