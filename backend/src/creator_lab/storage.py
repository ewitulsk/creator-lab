"""The data folder layout and disk accounting.

    data/
      db/creator_lab.sqlite
      media/{channel}/{video}/      video.mp4, audio.wav, thumb.jpg, captions.en.vtt, title.txt
      analyzers/{analyzer}/{version}/{video}/
      runner_cache/tribe/
      exports/
      backups/

Asset paths in the database are stored relative to DATA_ROOT, with forward slashes,
so the folder can move.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import threading
import time
from pathlib import Path

from flask import current_app

SUBDIRS = ("db", "media", "analyzers", "runner_cache", "exports", "backups", "logs")


def data_root() -> Path:
    return current_app.config["APP"].data_root


def ensure_layout(root: Path) -> None:
    for sub in SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)


def rel(path: Path) -> str:
    return path.resolve().relative_to(data_root().resolve()).as_posix()


def absolute(relpath: str) -> Path:
    path = (data_root() / relpath).resolve()
    if not path.is_relative_to(data_root().resolve()):  # no ../ escapes
        raise ValueError(f"path escapes the data folder: {relpath}")
    return path


def media_dir(channel_external_id: str, video_external_id: str) -> Path:
    return data_root() / "media" / channel_external_id / video_external_id


def analyzer_dir(name: str, version: str, video_external_id: str) -> Path:
    return data_root() / "analyzers" / name / version / video_external_id


def logs_dir() -> Path:
    return data_root() / "logs"


def folder_bytes(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):  # file vanished mid-walk
                total += os.stat(os.path.join(dirpath, name)).st_size
    return total


_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()
CACHE_SECONDS = 300


def disk_report(root: Path, refresh: bool = False) -> dict:
    """Drive free space plus the size of each top-level data subfolder (cached 5 min)."""
    key = str(root)
    with _lock:
        hit = _cache.get(key)
        if hit and not refresh and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    usage = shutil.disk_usage(root if root.exists() else root.anchor or ".")
    folders = {sub: folder_bytes(root / sub) for sub in SUBDIRS if (root / sub).exists()}
    report = {
        "drive_total": usage.total,
        "drive_free": usage.free,
        "drive_used": usage.used,
        "data_bytes": sum(folders.values()),
        "folders": folders,
        "measured_at": time.time(),
    }
    with _lock:
        _cache[key] = (time.time(), report)
    return report
