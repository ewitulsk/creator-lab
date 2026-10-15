"""SQLite backups with the online backup API (consistent while the app is running)."""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
from pathlib import Path

from flask import current_app

from .settings import get_setting

log = logging.getLogger(__name__)
PREFIX = "creator_lab-"


def backups_dir() -> Path:
    return current_app.config["APP"].data_root / "backups"


def list_backups() -> list[dict]:
    folder = backups_dir()
    if not folder.exists():
        return []
    out = []
    for p in sorted(folder.glob(f"{PREFIX}*.sqlite"), reverse=True):
        st = p.stat()
        out.append(
            {
                "name": p.name,
                "bytes": st.st_size,
                "created_at": dt.datetime.fromtimestamp(st.st_mtime, dt.UTC).isoformat(timespec="seconds"),
            }
        )
    return out


def backup_now() -> dict:
    cfg = current_app.config["APP"]
    folder = backups_dir()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = folder / f"{PREFIX}{stamp}.sqlite"
    src = sqlite3.connect(str(cfg.db_path))
    dst = sqlite3.connect(str(target))
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    prune(get_setting("backups_to_keep"))
    log.info("database backed up to %s", target)
    return {"name": target.name, "bytes": target.stat().st_size}


def prune(keep: int) -> list[str]:
    removed = []
    for b in list_backups()[keep:]:
        (backups_dir() / b["name"]).unlink(missing_ok=True)
        removed.append(b["name"])
    return removed


def due() -> bool:
    latest = list_backups()[:1]
    if not latest:
        return True
    created = dt.datetime.fromisoformat(latest[0]["created_at"])
    return dt.datetime.now(dt.UTC) - created >= dt.timedelta(hours=get_setting("backup_interval_hours"))
