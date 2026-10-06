"""The next run of a trigger, as the API reports it (B1-13).

``schedules.next_fire_at`` is the beat's internal "evaluate again at" hint: it
holds a 10-minute claim lease while a tick processes the row, the next cron
slot of a business_calendar even when that slot is a holiday or at night, and
9999-01-01 for a fired one-shot. The API answered it as ``next_fire_at``, so a
new weekday-9am cron could read "next run 01:40". This computes the real next
run of the time-family triggers from the trigger itself; other types keep the
stored value (their next poll / evaluation).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from app.triggers.time_math import is_business_time, resolve_tz

# Bounded search for the next business-hours slot of a business_calendar
# (each step jumps to the next business window, so this is ~days, not slots).
_BUSINESS_SLOT_SEARCH = 2_000
# As app.scaling.tasks._ON_TIME_GRACE_SECONDS (catch_up="none").
_ON_TIME_GRACE_SECONDS = 90


def _aware(value: Any) -> dt.datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        try:
            parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _next_tick(now: dt.datetime) -> dt.datetime:
    """The beat's next tick: second 0 of the next minute (B1-7)."""
    return (now + dt.timedelta(minutes=1)).replace(second=0, microsecond=0)


def _cron_next(expr: str, tz_name: str, after: dt.datetime) -> dt.datetime:
    from croniter import croniter

    nxt = croniter(expr, after.astimezone(resolve_tz(tz_name))).get_next(dt.datetime)
    return _aware(nxt).astimezone(dt.UTC)  # type: ignore[union-attr]


def _next_window_start(
    slot: dt.datetime, tz_name: str, calendar: dict[str, Any]
) -> dt.datetime:
    """Just before the next business window opening at or after *slot*'s local
    time: the same day's opening when *slot* is earlier, else the next day's."""
    tz = resolve_tz(tz_name)
    local = slot.astimezone(tz)
    hh, mm = (int(x) for x in str(calendar.get("business_hours_start") or "09:00").split(":"))
    opening = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if local >= opening:
        opening = (opening + dt.timedelta(days=1)).replace(hour=hh, minute=mm)
    return opening.astimezone(dt.UTC) - dt.timedelta(seconds=1)


def next_run_at(rec: dict[str, Any], now: dt.datetime | None = None) -> dt.datetime | None:
    """When *rec*'s trigger next runs (UTC), or None when nothing is scheduled
    (paused, a fired one-shot, an event-armed delay, no business slot soon)."""
    spec = rec.get("spec")
    stored = rec.get("next_fire_at")
    if spec is None:
        return _aware(stored)
    now = now or dt.datetime.now(dt.UTC)
    ttype = str(getattr(spec.trigger_type, "value", spec.trigger_type))
    if ttype not in {"cron", "business_calendar", "interval", "once", "relative_delay",
                     "deadline"}:
        return _aware(stored)
    if rec.get("paused"):
        return None
    try:
        if ttype == "cron":
            return _cron_next(spec.cron_expression, spec.timezone, now)
        if ttype == "business_calendar":
            calendar = {
                "holidays": spec.holidays,
                "business_days": spec.business_days,
                "business_hours_start": spec.business_hours_start,
                "business_hours_end": spec.business_hours_end,
            }
            after = now
            for _ in range(_BUSINESS_SLOT_SEARCH):
                slot = _cron_next(spec.cron_expression, spec.timezone, after)
                if is_business_time(slot, spec.timezone, calendar):
                    return slot
                # Jump to the next business window instead of walking every slot.
                after = max(slot, _next_window_start(slot, spec.timezone, calendar))
            return None
        last = _aware(rec.get("last_fired_at"))
        if ttype == "interval":
            if last is None:
                return _next_tick(now)
            due = last + dt.timedelta(seconds=int(spec.interval_seconds or 0))
            return due if due > now else _next_tick(now)
        # once / relative_delay / deadline
        if last is not None or (ttype == "relative_delay" and not spec.fire_at_iso.strip()):
            return None
        base = _aware(spec.fire_at_iso)
        if base is None:
            return None
        if ttype == "relative_delay":
            base += dt.timedelta(seconds=int(spec.relative_offset_seconds or 0))
        elif ttype == "deadline":
            base -= dt.timedelta(seconds=int(spec.deadline_warning_seconds or 0))
        if base > now:
            return base
        # Missed: fires late on the next tick, unless catch_up="none" (B1-5).
        stale = (now - base).total_seconds() > _ON_TIME_GRACE_SECONDS
        return None if stale and spec.catch_up == "none" else _next_tick(now)
    except Exception:
        return _aware(stored)
