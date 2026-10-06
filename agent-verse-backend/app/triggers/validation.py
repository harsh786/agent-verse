"""Per-type trigger-spec validation.

The dispatch-map guard rejects *unknown/undispatchable* trigger types, but a
recognised type can still be misconfigured — a ``cron`` with no expression, an
``interval`` of 0s, an ``api_poll`` with no URL — which then silently never fires
correctly in production. ``validate_spec`` fails fast at create/update time with a
clear, actionable message (surfaced as HTTP 422) so a broken trigger can never be
persisted.

Only fields that are genuinely REQUIRED for a type to function are enforced here;
optional filters (event filters, monitoring label selectors, goal-chain watch
filters that legitimately default to "any") are intentionally not required.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app.triggers.models import TriggerSpec, check_plan_interval, validate_cron

_PRIORITIES = {"high", "normal", "low"}
# Missed-run policies of the time triggers (B1-5), see app.scaling.tasks._apply_catch_up.
CATCH_UP_POLICIES = ("all", "latest", "none")


def _is_iso(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


_SAFE_TABLE = re.compile(r"^[a-z_][a-z0-9_]*$")


def db_row_change_allowlist() -> frozenset[str]:
    """Tables DB_ROW_CHANGE may poll (settings ``db_row_change_tables``); only
    bare identifiers count, exactly as the beat checks before querying."""
    from app.core.config import get_settings

    raw = getattr(get_settings(), "db_row_change_tables", "") or ""
    return frozenset(t for t in (p.strip() for p in raw.split(",")) if _SAFE_TABLE.match(t))


_SAFE_TENANT = re.compile(r"^[A-Za-z0-9_-]+$")


def file_drop_root() -> str:
    from app.core.config import get_settings

    return str(getattr(get_settings(), "file_drop_root", "") or "").strip()


def file_drop_path_error(path: str) -> str | None:
    """Why *path* is not an acceptable ``file_drop_path`` (TRG-32), else None.

    It must be a folder relative to the tenant's drop root: absolute paths,
    ``..``, ``~`` and backslashes are refused, and the type is disabled when the
    operator has not configured ``FILE_DROP_ROOT``.
    """
    if not file_drop_root():
        return "file_drop triggers are disabled: the operator has not configured FILE_DROP_ROOT"
    p = path.strip()
    if not p:
        return "file_drop trigger requires file_drop_path"
    parts = p.split("/")
    if p.startswith(("/", "~")) or "\\" in p or ":" in p or ".." in parts:
        return (
            "file_drop_path must be a folder relative to your tenant drop folder "
            "(no absolute paths, '..', '~' or backslashes)"
        )
    return None


def resolve_file_drop_dir(tenant_id: str, path: str) -> str | None:
    """The real directory a file_drop trigger may list, or None (TRG-32).

    Resolved with ``realpath`` under ``<FILE_DROP_ROOT>/<tenant_id>`` at fire
    time, so a symlink (or a path stored before validation existed) cannot
    escape the tenant's folder.
    """
    import os

    root = file_drop_root()
    if not root or not _SAFE_TENANT.match(tenant_id or "") or file_drop_path_error(path):
        return None
    base = os.path.realpath(os.path.join(root, tenant_id))
    target = os.path.realpath(os.path.join(base, path.strip()))
    return target if is_within(target, base) else None


def is_within(path: str, base: str) -> bool:
    import os

    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def timezone_error(tz_name: str) -> str | None:
    """Why *tz_name* is not a usable IANA timezone, else None (empty = UTC).

    The beat resolves the zone with ``ZoneInfo`` and fell back to UTC for any
    name it could not load (B1-2), so a typo ran the schedule hours off.
    """
    name = (tz_name or "").strip()
    if not name or name.upper() == "UTC":
        return None
    from zoneinfo import ZoneInfo

    try:
        ZoneInfo(name)
    except (ValueError, KeyError, OSError):
        return (
            f"Unknown timezone {name!r}: use an IANA zone name such as "
            "'Asia/Kolkata', 'America/New_York' or 'UTC'"
        )
    return None


_EVENT_CHANNEL = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


def _validate_relative_delay(spec: TriggerSpec) -> None:
    """A fixed base (``fire_at_iso``) or, B1-8, an event base: each event on
    ``event_channel`` arms a fire ``relative_offset_seconds`` after the event
    (or after the timestamp at ``relative_to_field`` in its payload)."""
    from app.triggers.delayed import MAX_EVENT_DELAY_SECONDS

    if spec.fire_at_iso.strip():
        if spec.event_channel.strip():
            raise ValueError(
                "relative_delay takes either fire_at_iso (a fixed base time) or "
                "event_channel (the offset counts from each event), not both"
            )
        _require_iso(spec.fire_at_iso, "")
        return
    channel = spec.event_channel.strip()
    if not channel:
        raise ValueError(
            "relative_delay trigger requires fire_at_iso (a fixed base time) or "
            "event_channel (fire relative_offset_seconds after each event on it)"
        )
    if not _EVENT_CHANNEL.match(channel):
        raise ValueError(
            "event_channel may only contain letters, digits and . _ : - (at most 200)"
        )
    if not 0 <= int(spec.relative_offset_seconds or 0) <= MAX_EVENT_DELAY_SECONDS:
        raise ValueError(
            "relative_offset_seconds must be between 0 and "
            f"{MAX_EVENT_DELAY_SECONDS} for an event-relative delay"
        )
    field = spec.relative_to_field.strip()
    if field and not re.fullmatch(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*", field):
        raise ValueError("relative_to_field must be a dotted payload path, e.g. order.shipped_at")


MAX_HOLIDAYS = 500
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _validate_business_calendar(spec: TriggerSpec) -> None:
    """Holidays, business days and hours of a business_calendar trigger (B1-6)."""
    holidays = spec.holidays
    if not isinstance(holidays, list) or len(holidays) > MAX_HOLIDAYS:
        raise ValueError(f"holidays must be a list of at most {MAX_HOLIDAYS} YYYY-MM-DD dates")
    for day in holidays:
        try:
            datetime.strptime(str(day), "%Y-%m-%d")
        except ValueError:
            raise ValueError(f"holidays: {day!r} is not a YYYY-MM-DD date") from None
    days = spec.business_days
    if (
        not isinstance(days, list)
        or not days
        or any(not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 6 for d in days)
    ):
        raise ValueError("business_days must list weekday numbers 0 (Monday) to 6 (Sunday)")
    start, end = spec.business_hours_start, spec.business_hours_end
    if not (_HHMM.match(str(start)) and _HHMM.match(str(end))) or str(start) >= str(end):
        raise ValueError(
            "business_hours_start / business_hours_end must be HH:MM with start before end"
        )


def _require_iso(value: str, missing: str) -> None:
    if not value.strip():
        raise ValueError(missing)
    if not _is_iso(value):
        raise ValueError(f"fire_at_iso is not a valid ISO datetime: {value!r}")


def is_trigger_expired(expires_at_iso: object, *, now: datetime | None = None) -> bool:
    """True when a trigger's ``expires_at_iso`` is at or before *now* (TRG-09).

    A naive timestamp (and a naive *now*) is read as UTC. An empty value never
    expires; an unparseable one is treated as expired — create-time validation
    rejects it, so a stored one is corrupt and must not keep firing.
    """
    raw = str(expires_at_iso or "").strip()
    if not raw:
        return False
    try:
        expires = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return expires <= current


def creatable_error(spec: TriggerSpec, *, plan: str = "free") -> str | None:
    """Why *spec* must not be stored, or ``None`` when it can be (TRG-10).

    The one gate every schedule-creating path shares (POST /schedules, NL,
    chat): the type must have a runtime dispatch path and the configuration
    must pass :func:`validate_spec`; otherwise the trigger would never fire.
    """
    from app.triggers.dispatch_map import is_supported, unsupported_reason

    if not is_supported(spec.trigger_type):
        return unsupported_reason(spec.trigger_type)
    try:
        validate_spec(spec, plan=plan)
    except ValueError as exc:
        return str(exc)
    return None


def validate_spec(spec: TriggerSpec, *, plan: str = "free") -> None:
    """Validate cross-cutting options + type-specific required fields.

    Raises ``ValueError`` with a human-readable message on the first problem.
    """
    tt = spec.trigger_type
    v = tt.value if hasattr(tt, "value") else str(tt)

    # ── Cross-cutting options (apply to every type) ──────────────────────────
    if spec.max_firings_per_hour < 0:
        raise ValueError("max_firings_per_hour must be >= 0 (0 = unlimited)")
    if spec.priority not in _PRIORITIES:
        raise ValueError("priority must be one of: high, normal, low")
    if spec.expires_at_iso and not _is_iso(spec.expires_at_iso):
        raise ValueError(f"expires_at_iso is not a valid ISO datetime: {spec.expires_at_iso!r}")
    # Tenant regexes (chat keyword / email / phone filters) must be safe to run
    # on the event loop (TRG-20).
    from app.triggers.consumers.conversational import validate_conversational_patterns

    validate_conversational_patterns(spec)
    if spec.catch_up not in CATCH_UP_POLICIES:
        raise ValueError(
            f"catch_up must be one of: {', '.join(CATCH_UP_POLICIES)} (got {spec.catch_up!r})"
        )
    # B1-12: a condition the evaluator cannot run was stored, and every fire
    # was then skipped as condition_error.
    conditions = (("condition_cel", spec.condition_expression), ("condition", spec.condition))
    for label, expr in conditions:
        if expr and expr.strip():
            from app.triggers.condition.evaluator import CELEvaluator

            try:
                CELEvaluator().check(expr)
            except ValueError as exc:
                raise ValueError(
                    f"{label} cannot be evaluated ({exc}); conditions support payload fields "
                    "(payload.a.b), literals, == != < <= > >=, in, and/or/not (&& || !)"
                ) from exc
    # B1-2: an unknown zone used to be stored and then silently evaluated as UTC.
    if (reason := timezone_error(spec.timezone)) is not None:
        raise ValueError(reason)
    # TRG-23: HITL queue filters are derived ids (agent:<id> / risk:<tier>).
    if spec.hitl_queue_id:
        from app.governance.hitl_queues import queue_id_error

        if (reason := queue_id_error(spec.hitl_queue_id)) is not None:
            raise ValueError(reason)

    # B7-5: the 0.0 default can never be undercut, so the trigger never fired.
    if v == "goal_score_below" and not 0.0 < float(spec.score_threshold or 0.0) <= 1.0:
        raise ValueError(
            "goal_score_below trigger requires a score_threshold in (0, 1] "
            "(it fires when the goal's score is below it)"
        )

    # ── Type-specific required fields ────────────────────────────────────────
    if v == "cron":
        if not spec.cron_expression.strip():
            raise ValueError("cron trigger requires a cron_expression")
        validate_cron(spec.cron_expression, plan)
    elif v == "interval":
        if spec.interval_seconds <= 0:
            raise ValueError("interval trigger requires interval_seconds > 0")
        # TRG-16: the same per-plan floor as cron.
        check_plan_interval(spec.interval_seconds, plan)
    elif v == "once":
        if not spec.fire_at_iso.strip():
            raise ValueError("once trigger requires fire_at_iso")
        if not _is_iso(spec.fire_at_iso):
            raise ValueError(f"fire_at_iso is not a valid ISO datetime: {spec.fire_at_iso!r}")
    # TRG-08: require exactly what the beat reads. relative_to_field /
    # deadline_field / business_calendar_id alone passed validation but the
    # beat fires only from fire_at_iso / cron_expression, so they never fired.
    elif v == "relative_delay":
        _validate_relative_delay(spec)
    elif v == "deadline":
        _require_iso(
            spec.fire_at_iso,
            "deadline trigger requires fire_at_iso (the deadline); payload deadlines "
            "(deadline_field) are not supported yet",
        )
    elif v == "business_calendar":
        if not spec.cron_expression.strip():
            raise ValueError(
                "business_calendar trigger requires a cron_expression (fired only in "
                "business hours); business_calendar_id alone never fires"
            )
        validate_cron(spec.cron_expression, plan)
        _validate_business_calendar(spec)
    elif v == "api_poll":
        if not spec.poll_url.strip():
            raise ValueError("api_poll trigger requires poll_url")
        if spec.poll_interval_seconds <= 0:
            raise ValueError("api_poll trigger requires poll_interval_seconds > 0")
        # TRG-33: the beat now honours the interval; it gets the plan floor too.
        check_plan_interval(spec.poll_interval_seconds, plan)
    elif v == "rss_feed":
        if not spec.rss_url.strip():
            raise ValueError("rss_feed trigger requires rss_url")
    elif v == "db_row_change":
        if not spec.db_table.strip():
            raise ValueError("db_row_change trigger requires db_table")
        allowed = db_row_change_allowlist()
        if spec.db_table not in allowed:
            raise ValueError(
                f"db_table {spec.db_table!r} is not in the operator's db_row_change "
                "allowlist (DB_ROW_CHANGE_TABLES), so it would never be polled; allowed: "
                + (", ".join(sorted(allowed)) or "none configured")
            )
    elif v == "file_drop":
        if (reason := file_drop_path_error(spec.file_drop_path)) is not None:
            raise ValueError(reason)
    elif v == "condition":
        if not (spec.condition_expression.strip() or spec.condition.strip()):
            raise ValueError("condition trigger requires condition_expression (a CEL expression)")
    elif v == "counter_threshold":
        if not spec.counter_key.strip():
            raise ValueError("counter_threshold trigger requires counter_key")
        if spec.counter_threshold <= 0:
            raise ValueError("counter_threshold trigger requires counter_threshold > 0")
    elif v == "window_aggregate":
        if not spec.window_field.strip():
            raise ValueError("window_aggregate trigger requires window_field")
        if spec.window_seconds <= 0:
            raise ValueError("window_aggregate trigger requires window_seconds > 0")
    elif v == "compound":
        if not spec.compound_trigger_ids:
            raise ValueError("compound trigger requires compound_trigger_ids")
    elif v == "state_transition":
        if not spec.state_machine_id.strip():
            raise ValueError("state_transition trigger requires state_machine_id")
    elif v == "chat_command":
        if not spec.command_pattern.strip():
            raise ValueError("chat_command trigger requires command_pattern")
    elif v == "chat_keyword":
        if not spec.keyword_pattern.strip():
            raise ValueError("chat_keyword trigger requires keyword_pattern")
    elif v == "form_submission":
        if not spec.form_id.strip():
            raise ValueError("form_submission trigger requires form_id")
    elif v == "price_threshold":
        if not spec.price_symbol.strip():
            raise ValueError("price_threshold trigger requires price_symbol")
    elif v == "mqtt":
        if not spec.mqtt_topic.strip():
            raise ValueError("mqtt trigger requires mqtt_topic")
    elif v == "sensor_threshold":
        if not spec.sensor_device_id.strip() or not spec.sensor_metric.strip():
            raise ValueError("sensor_threshold trigger requires sensor_device_id and sensor_metric")
    elif v == "geofence":
        if not spec.geofence_polygon:
            raise ValueError("geofence trigger requires geofence_polygon")
