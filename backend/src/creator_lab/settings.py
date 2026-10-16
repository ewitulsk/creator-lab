"""Editable settings with defaults, stored in the settings table."""

from __future__ import annotations

from typing import Any

from .extensions import db
from .models import Setting

DEFAULTS: dict[str, Any] = {
    # Video snapshots: daily while young, weekly after.
    "snapshot_young_days": 30,
    "snapshot_young_interval_hours": 24,
    "snapshot_old_interval_hours": 168,
    # Channel snapshots and new-upload checks.
    "channel_snapshot_interval_hours": 24,
    "new_upload_check_hours": 6,
    # Scheduler tick and pacing.
    "scheduler_tick_minutes": 15,
    "daily_quota_budget": 9000,  # of the 10,000 free units; leaves room for manual adds
    "runner_timeout_minutes": 10,
    "download_pause_seconds": 20,
    "backup_interval_hours": 24,
    "backups_to_keep": 7,
}

# Validation: (min, max) for each numeric setting.
LIMITS: dict[str, tuple[int, int]] = {
    "snapshot_young_days": (1, 365),
    "snapshot_young_interval_hours": (1, 24 * 30),
    "snapshot_old_interval_hours": (1, 24 * 90),
    "channel_snapshot_interval_hours": (1, 24 * 30),
    "new_upload_check_hours": (1, 24 * 7),
    "scheduler_tick_minutes": (1, 24 * 60),
    "daily_quota_budget": (100, 10_000),
    "runner_timeout_minutes": (1, 24 * 60),
    "download_pause_seconds": (0, 3600),
    "backup_interval_hours": (1, 24 * 30),
    "backups_to_keep": (1, 100),
}


def get_setting(key: str) -> Any:
    row = db.session.get(Setting, key)
    return row.value if row is not None else DEFAULTS[key]


def all_settings() -> dict[str, Any]:
    stored = {s.key: s.value for s in db.session.scalars(db.select(Setting))}
    return {k: stored.get(k, v) for k, v in DEFAULTS.items()}


def update_settings(values: dict[str, Any]) -> dict[str, Any]:
    """Validate and save. Raises ValueError naming the first bad key."""
    clean = {}
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ValueError(f"unknown setting {key!r}")
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be a whole number")
        lo, hi = LIMITS[key]
        if not lo <= value <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        clean[key] = value
    for key, value in clean.items():
        row = db.session.get(Setting, key)
        if row is None:
            db.session.add(Setting(key=key, value=value))
        else:
            row.value = value
    db.session.commit()
    return all_settings()
