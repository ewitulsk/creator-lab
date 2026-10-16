"""yt-dlp info dicts: sanitize them for saving, and pull out the 'Most replayed' heatmap,
chapters and a stats snapshot."""

from __future__ import annotations

import json
import time
from pathlib import Path

# Never persist request headers or cookies (yt-dlp keeps them on formats and at the top level).
SECRET_KEYS = {"http_headers", "cookies", "_cookies", "__x_forwarded_for_ip"}
# Bulky fields dropped from the info saved by heatmap-only jobs (no download).
BULKY_KEYS = {"formats", "requested_formats", "thumbnails", "automatic_captions", "subtitles", "requested_subtitles"}
SNAPSHOT_FIELDS = (
    "view_count",
    "like_count",
    "comment_count",
    "channel_follower_count",
    "duration",
    "availability",
    "live_status",
    "was_live",
    "media_type",
    "age_limit",
    "categories",
    "tags",
    "upload_date",
    "timestamp",
    "release_timestamp",
    "title",
)


def scrub(obj, drop: set[str] | frozenset[str] = frozenset()):
    """Remove secret (and optionally bulky) keys at any depth."""
    if isinstance(obj, dict):
        return {k: scrub(v, drop) for k, v in obj.items() if k not in SECRET_KEYS and k not in drop}
    if isinstance(obj, list):
        return [scrub(v, drop) for v in obj]
    return obj


def save_info(info: dict, path: Path, compact: bool = False) -> Path:
    """info is expected to have gone through YoutubeDL.sanitize_info already."""
    data = scrub(info, BULKY_KEYS if compact else frozenset())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def capture_from_info(info: dict) -> dict:
    """The parts the platform stores as tables: heatmap, chapters and a stats snapshot."""
    heatmap = []
    for seg in info.get("heatmap") or []:
        try:
            heatmap.append(
                {
                    "start_s": float(seg["start_time"]),
                    "end_s": float(seg["end_time"]),
                    "value": float(seg["value"]),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    chapters = []
    for ch in info.get("chapters") or []:
        try:
            chapters.append(
                {
                    "start_s": float(ch["start_time"]),
                    "end_s": float(ch["end_time"]) if ch.get("end_time") is not None else None,
                    "title": str(ch.get("title") or ""),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "heatmap": sorted(heatmap, key=lambda s: s["start_s"]),
        "chapters": sorted(chapters, key=lambda c: c["start_s"]),
        "snapshot": {k: info.get(k) for k in SNAPSHOT_FIELDS if info.get(k) is not None},
    }
