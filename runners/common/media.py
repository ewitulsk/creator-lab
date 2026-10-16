"""ffmpeg/ffprobe helpers (subprocess only)."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path


def ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg is not on PATH (winget install Gyan.FFmpeg)")
    return exe


def ffprobe_exe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe is not on PATH (it ships with ffmpeg)")
    return exe


def probe(path: Path) -> dict:
    """Duration, size and first video/audio stream codecs of a media file."""
    out = subprocess.run(
        [ffprobe_exe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(out.stdout)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {})
    fmt = data.get("format", {})
    return {
        "duration": float(fmt["duration"]) if fmt.get("duration") else None,
        "vcodec": video.get("codec_name"),
        "acodec": audio.get("codec_name"),
        "width": video.get("width"),
        "height": video.get("height"),
        "fps": _fps(video.get("avg_frame_rate")),
        "bitrate": int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
    }


def _fps(rate: str | None) -> float | None:
    if not rate or rate == "0/0":
        return None
    num, _, den = rate.partition("/")
    return round(float(num) / float(den or 1), 3)


def sha256(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def run_ffmpeg(args: list[str], duration: float | None, on_progress: Callable[[float], None] | None = None) -> None:
    """Run ffmpeg, calling on_progress(percent) as it goes (from -progress output)."""
    cmd = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:
        if on_progress and duration and line.startswith("out_time_us="):
            try:
                us = int(line.split("=", 1)[1])
                on_progress(min(100.0, 100 * us / 1e6 / duration))
            except ValueError:
                pass
    err = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed: {err.strip()[-2000:]}")


def encode_h264(src: Path, dst: Path, max_height: int, duration: float | None, on_progress=None) -> None:
    """Re-encode to browser- and TRIBE-friendly H.264/AAC mp4, never taller than max_height."""
    run_ffmpeg(
        [
            "-i",
            str(src),
            "-vf",
            f"scale=-2:'min(ih,{max_height})'",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "21",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(dst),
        ],
        duration,
        on_progress,
    )


def remux_faststart(src: Path, dst: Path) -> None:
    """Copy streams into an mp4 with the index up front, so browsers can seek right away."""
    run_ffmpeg(["-i", str(src), "-c", "copy", "-movflags", "+faststart", str(dst)], None)
