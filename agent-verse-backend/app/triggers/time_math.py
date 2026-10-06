"""Pure time helpers of the time-family triggers, shared by the beat
(app.scaling.tasks) and the API's next-run display (app.triggers.next_run)."""

from __future__ import annotations

import datetime
from typing import Any

DEFAULT_BUSINESS_DAYS = (0, 1, 2, 3, 4)


def resolve_tz(tz_name: str) -> datetime.tzinfo:
    """The zone for *tz_name*; UTC for an empty or unloadable name (validation
    refuses unloadable names on save, B1-2)."""
    if not tz_name or tz_name.upper() == "UTC":
        return datetime.UTC
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz_name)
    except Exception:
        return datetime.UTC


def is_business_time(
    dt_utc: datetime.datetime,
    tz_name: str = "UTC",
    calendar: dict[str, Any] | None = None,
) -> bool:
    """True when the instant is a business moment of *calendar*, in local time.

    Default: Mon-Fri, 09:00-17:00. B1-6: ``business_days`` (Monday = 0),
    ``business_hours_start`` / ``_end`` (local "HH:MM", end exclusive) and
    ``holidays`` (local "YYYY-MM-DD" dates) come from the trigger.
    """
    cal = calendar or {}
    tz = resolve_tz(tz_name)
    aware = dt_utc.replace(tzinfo=datetime.UTC) if dt_utc.tzinfo is None else dt_utc
    local = aware.astimezone(tz)
    days = cal.get("business_days") or DEFAULT_BUSINESS_DAYS
    if local.weekday() not in {int(d) for d in days}:
        return False
    if local.date().isoformat() in {str(h) for h in cal.get("holidays") or ()}:
        return False
    hhmm = local.strftime("%H:%M")
    start = str(cal.get("business_hours_start") or "09:00")
    end = str(cal.get("business_hours_end") or "17:00")
    return start <= hhmm < end
