"""The blinded upload copy: low resolution, low frame rate, no metadata, random name."""

from __future__ import annotations

import secrets
import shutil
import subprocess
from pathlib import Path

# The Files API takes up to 2 GB per file on the free tier (20 GB paid). Stay under the lower one.
MAX_UPLOAD_BYTES = 2 * 1024**3


def blind_copy_command(
    src: Path, dest_dir: Path, height: int, fps: int, ffmpeg: str = "ffmpeg"
) -> tuple[list[str], Path]:
    """ffmpeg arguments for the upload copy, and the random output path.

    Strips all container, stream and chapter metadata (no title, no encoder tag), scales to
    `height` (never up), samples `fps` frames per second at a low bitrate and keeps a mono
    audio track. The file name is random so nothing about the video leaks through it.
    """
    out = dest_dir / f"{secrets.token_hex(12)}.mp4"
    cmd = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        "-map_metadata", "-1", "-map_metadata:s:v", "-1", "-map_metadata:s:a", "-1", "-map_chapters", "-1",
        "-vf", f"scale=-2:'min({int(height)},ih)',fps={int(fps)}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "32", "-maxrate", "400k", "-bufsize", "800k",
        "-c:a", "aac", "-b:a", "48k", "-ac", "1",
        "-fflags", "+bitexact", "-flags:v", "+bitexact", "-flags:a", "+bitexact",
        "-movflags", "+faststart",
        str(out),
    ]  # fmt: skip
    return cmd, out


def make_blind_copy(src: Path, dest_dir: Path, height: int, fps: int, run=subprocess.run) -> Path:
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    dest_dir.mkdir(parents=True, exist_ok=True)
    cmd, out = blind_copy_command(src, dest_dir, height, fps, ffmpeg)
    proc = run(cmd, capture_output=True, text=True, errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed making the upload copy: {(proc.stderr or '')[-300:]}")
    size = out.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        out.unlink(missing_ok=True)
        raise RuntimeError(
            f"the upload copy is {size / 1e9:.1f} GB, over the 2 GB Files API limit; lower upload_height"
        )
    return out
