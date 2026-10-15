"""Video downloads with yt-dlp, capped at MAX_VIDEO_HEIGHT. 4K is never stored.

Prefers native H.264 + AAC at or below the cap, so no re-encode is needed and the same
file serves the browser and TRIBE. Anything else (AV1, VP9, Opus) is re-encoded to
H.264/AAC at the capped height with ffmpeg.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from ..common.media import encode_h264, probe, remux_faststart, sha256
from ..common.runner import Reporter, RetryableError

log = logging.getLogger("runner.download")

THROTTLE_HINTS = ("HTTP Error 429", "Too Many Requests", "Sign in to confirm", "rate-limit")


def format_selector(max_height: int) -> str:
    """Never select a stream taller than max_height."""
    h = max_height
    return (
        f"bv*[vcodec^=avc1][height<={h}]+ba[acodec^=mp4a]/"
        f"b[vcodec^=avc1][height<={h}][acodec^=mp4a]/"
        f"bv*[height<={h}]+ba/"
        f"b[height<={h}]"
    )


def needs_reencode(info: dict, max_height: int) -> bool:
    return (
        info.get("vcodec") != "h264"
        or info.get("acodec") not in ("aac", None)
        or (info.get("height") or 0) > max_height
    )


def download_one(item: dict, max_height: int, reporter: Reporter) -> dict:
    import yt_dlp

    out_dir = Path(item["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / "video.mp4"
    for stale in out_dir.glob("video.download.*"):
        stale.unlink(missing_ok=True)

    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            if total:
                speed = d.get("speed") or 0
                reporter.update(
                    stage_percent=80 * done / total,
                    message=f"{done / 1e6:.0f} of {total / 1e6:.0f} MB"
                    + (f" at {speed / 1e6:.1f} MB/s" if speed else ""),
                )

    opts = {
        "format": format_selector(max_height),
        "merge_output_format": "mp4",
        "outtmpl": str(out_dir / "video.download.%(ext)s"),
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 4,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(item["url"], download=True)
    except yt_dlp.utils.DownloadError as err:
        msg = str(err)
        if any(h in msg for h in THROTTLE_HINTS):
            raise RetryableError(f"YouTube is throttling downloads: {msg[:300]}") from err
        raise
    raw = next((p for p in out_dir.glob("video.download.*") if p.suffix != ".part"), None)
    if raw is None:
        raise RuntimeError("yt-dlp finished but no file was written")

    meta = probe(raw)
    if needs_reencode(meta, max_height):
        log.info(
            "re-encoding %s (%s/%s, %sp) to H.264/AAC <=%sp",
            item["external_id"],
            meta["vcodec"],
            meta["acodec"],
            meta["height"],
            max_height,
        )
        reporter.update(stage_percent=80, message="re-encoding to H.264")
        encode_h264(raw, final, max_height, meta["duration"], lambda p: reporter.update(stage_percent=80 + 0.2 * p))
    else:
        reporter.update(stage_percent=85, message="finalizing")
        remux_faststart(raw, final)
    raw.unlink(missing_ok=True)

    meta = probe(final)
    if (meta["height"] or 0) > max_height:  # belt and braces: never keep 4K
        final.unlink(missing_ok=True)
        raise RuntimeError(f"downloaded file is {meta['height']}p, above the {max_height}p cap")
    reporter.update(stage_percent=95, message="hashing")
    live_status = info.get("live_status")
    result = {
        "status": "done",
        "output_path": str(final),
        "summary": {
            "height": meta["height"],
            "width": meta["width"],
            "fps": meta["fps"],
            "duration_s": meta["duration"],
            "vcodec": meta["vcodec"],
            "acodec": meta["acodec"],
            "bytes": final.stat().st_size,
            "source_format": info.get("format_id"),
            "live_status": live_status,
        },
        "assets": [
            {
                "kind": "video",
                "path": str(final),
                "bytes": final.stat().st_size,
                "sha256": sha256(final),
                "width": meta["width"],
                "height": meta["height"],
                "codec": f"{meta['vcodec']}/{meta['acodec']}",
                "meta": {"fps": meta["fps"], "duration_s": meta["duration"], "yt_format": info.get("format_id")},
            }
        ],
    }
    if live_status in ("was_live", "post_live", "is_live"):
        result["video_updates"] = {"format": "live"}
    return result


def run_downloads(job: dict, reporter: Reporter) -> None:
    max_height = int(job["max_video_height"])
    pause = float(job.get("download_pause_seconds") or 0)
    items = job["items"]
    throttled = 0
    for i, item in enumerate(items, 1):
        if item["status"] == "done":
            continue
        reporter.video(i, item["video_id"], f"downloading {item['title'][:60]}")
        try:
            result = download_one(item, max_height, reporter)
        except RetryableError:
            throttled += 1
            if throttled >= 2:
                raise  # stop hammering YouTube; the platform requeues the job
            reporter.client.item_result(job["id"], item["video_id"], {"status": "failed", "error": "throttled"})
            continue
        except Exception as err:
            log.error("download failed for %s: %s", item["external_id"], err)
            reporter.client.item_result(job["id"], item["video_id"], {"status": "failed", "error": str(err)[:1000]})
            continue
        reporter.update(stage_percent=100, message="done", force=True)
        reporter.client.item_result(job["id"], item["video_id"], result)
        if pause and i < len(items):
            reporter.update(message=f"pausing {pause:.0f}s between downloads", force=True)
            end = time.monotonic() + pause
            while time.monotonic() < end:
                time.sleep(min(2.0, end - time.monotonic()))
                reporter.update()
