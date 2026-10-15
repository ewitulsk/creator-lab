"""Lever-scoring settings (stored in the settings table under a `levers.` prefix).

Model ids were checked against google-genai 2.31's model list and Google's docs
(2026-10-15): both support agentic video. The key is only ever reported as set/unset.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from flask import current_app

from ..config import is_set
from ..extensions import db
from ..models import Setting

PREFIX = "levers."

# Display name, then the API id. Change ids here only.
MODELS: dict[str, dict] = {
    "gemini-4-argon": {
        "title": "Gemini 4 Argon",
        # USD per 1M tokens; output includes thinking. (until, input, output); last entry has no end.
        "prices": [(dt.date(2026, 11, 30), 2.00, 10.00), (None, 4.00, 20.00)],
    },
    "gemini-3.8-flash": {
        "title": "Gemini 3.8 Flash",
        "prices": [(dt.date(2026, 12, 31), 0.75, 3.75), (None, 1.50, 7.50)],
    },
}
DEFAULT_MODEL = "gemini-4-argon"

DEFAULTS: dict[str, Any] = {
    "model": DEFAULT_MODEL,
    # upload: a blinded low-resolution copy (the default). youtube_url: NOT blinded, only for the A/B check.
    "input_mode": "upload",
    "upload_height": 360,
    "upload_fps": 2,
    "runs": 3,
    "temperature": 0.7,
    "agentic": True,
    "min_seconds_between_calls": 2,
}
CHOICES = {"model": list(MODELS), "input_mode": ["upload", "youtube_url"]}
INTS = {"upload_height": (144, 720), "upload_fps": (1, 10), "runs": (1, 9), "min_seconds_between_calls": (0, 120)}
FLOATS = {"temperature": (0.0, 2.0)}
BOOLS = {"agentic"}


def get_config() -> dict[str, Any]:
    stored = {
        s.key.removeprefix(PREFIX): s.value
        for s in db.session.scalars(db.select(Setting).where(Setting.key.like(PREFIX + "%")))
    }
    return {k: stored.get(k, v) for k, v in DEFAULTS.items()}


def update_config(values: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in values.items():
        if key not in DEFAULTS:
            raise ValueError(f"unknown lever setting {key!r}")
        if key in CHOICES:
            if value not in CHOICES[key]:
                raise ValueError(f"{key} must be one of {', '.join(CHOICES[key])}")
        elif key in INTS:
            lo, hi = INTS[key]
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                raise ValueError(f"{key} must be a whole number between {lo} and {hi}")
        elif key in FLOATS:
            lo, hi = FLOATS[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}")
            value = float(value)
        elif key in BOOLS and not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        clean[key] = value
    for key, value in clean.items():
        row = db.session.get(Setting, PREFIX + key)
        if row is None:
            db.session.add(Setting(key=PREFIX + key, value=value))
        else:
            row.value = value
    db.session.commit()
    return get_config()


def api_key() -> str | None:
    key = current_app.config["APP"].gemini_api_key
    return key if is_set(key) else None


def key_is_set() -> bool:
    return api_key() is not None


def price(model: str, on: dt.date | None = None) -> tuple[float, float]:
    """(input, output) USD per 1M tokens for this model on this date."""
    on = on or dt.date.today()
    for until, pin, pout in MODELS[model]["prices"]:
        if until is None or on <= until:
            return pin, pout
    raise AssertionError("price table must end with an open-ended entry")


def cost_usd(model: str, input_tokens: int, output_tokens: int, thought_tokens: int = 0) -> float:
    pin, pout = price(model)
    return round((input_tokens * pin + (output_tokens + thought_tokens) * pout) / 1e6, 6)
