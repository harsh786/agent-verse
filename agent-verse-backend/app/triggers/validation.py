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
        _require_iso(
            spec.fire_at_iso,
            "relative_delay trigger requires fire_at_iso (the base time the offset is "
            "added to); payload-relative delays (relative_to_field) are not supported yet",
        )
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
