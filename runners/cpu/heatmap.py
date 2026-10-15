"""The 'Most replayed' heatmap job: yt-dlp metadata only, no download.

Works on any public video (discovered ones and other creators' too). Saves a compact,
sanitized info.json next to where the video would go and reports the heatmap, chapters
and a stats snapshot for the platform to store. YouTube only shows the heatmap on videos
with enough views; for the rest the capture simply has no heatmap points.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from ..common.runner import Reporter, RetryableError
from .info import capture_from_info, save_info
from .ytdl import base_opts

log = logging.getLogger("runner.heatmap")


def fetch_info(url: str) -> dict:
    import yt_dlp

    with yt_dlp.YoutubeDL(base_opts(skip_download=True)) as ydl:
        try:
            return ydl.sanitize_info(ydl.extract_info(url, download=False))
        except yt_dlp.utils.DownloadError as err:
            if "429" in str(err) or "Too Many Requests" in str(err):
                raise RetryableError(str(err)[:300]) from err
            raise


def heatmap_result(item: dict, info: dict) -> dict:
    out_dir = Path(item["output_dir"])
    info_path = save_info(info, out_dir / "info.json", compact=True)
    capture = capture_from_info(info)
    return {
        "status": "done",
        "output_path": str(info_path),
        "summary": {
            "points": len(capture["heatmap"]),
            "chapters": len(capture["chapters"]),
            "available": bool(capture["heatmap"]),
        },
        "assets": [{"kind": "info", "path": str(info_path), "bytes": info_path.stat().st_size}],
        "capture": capture,
    }


def run_heatmaps(job: dict, reporter: Reporter, fetch=fetch_info) -> None:
    pause = min(float(job.get("download_pause_seconds") or 0), 5.0)  # metadata calls are light
    for i, item in enumerate(job["items"], 1):
        if item["status"] == "done":
            continue
        reporter.video(i, item["video_id"], f"heatmap for {item['title'][:60]}")
        try:
            result = heatmap_result(item, fetch(item["url"]))
        except RetryableError:
            raise
        except Exception as err:
            log.error("heatmap failed for %s: %s", item["external_id"], err)
            result = {"status": "failed", "error": str(err)[:1000]}
        reporter.update(stage_percent=100, force=True)
        reporter.client.item_result(job["id"], item["video_id"], result)
        if pause and i < len(job["items"]):
            time.sleep(pause)
