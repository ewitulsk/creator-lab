"""YouTube Data API quota accounting. The free quota is 10,000 units a day,
reset at midnight Pacific time."""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from ..extensions import db
from ..models import ApiUsage

PACIFIC = ZoneInfo("America/Los_Angeles")
API = "youtube_data"


class QuotaExceeded(RuntimeError):
    pass


def pacific_today() -> dt.date:
    return dt.datetime.now(PACIFIC).date()


def units_used_today() -> int:
    row = db.session.get(ApiUsage, (pacific_today(), API))
    return row.units if row else 0


def spend(units: int) -> None:
    """Record units spent. Commits on its own so usage survives a later rollback."""
    day = pacific_today()
    row = db.session.get(ApiUsage, (day, API))
    if row is None:
        row = ApiUsage(day=day, api=API, units=0, calls=0)
        db.session.add(row)
    row.units += units
    row.calls += 1
    db.session.commit()


def check_budget(units: int, budget: int) -> None:
    used = units_used_today()
    if used + units > budget:
        raise QuotaExceeded(f"YouTube quota budget reached ({used}/{budget} units used today)")
