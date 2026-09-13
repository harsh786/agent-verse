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

from datetime import datetime

from app.triggers.models import TriggerSpec, validate_cron

_PRIORITIES = {"high", "normal", "low"}


def _is_iso(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


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

    # ── Type-specific required fields ────────────────────────────────────────
    if v == "cron":
        if not spec.cron_expression.strip():
            raise ValueError("cron trigger requires a cron_expression")
        validate_cron(spec.cron_expression, plan)
    elif v == "interval":
        if spec.interval_seconds <= 0:
            raise ValueError("interval trigger requires interval_seconds > 0")
    elif v == "once":
        if not spec.fire_at_iso.strip():
            raise ValueError("once trigger requires fire_at_iso")
        if not _is_iso(spec.fire_at_iso):
            raise ValueError(f"fire_at_iso is not a valid ISO datetime: {spec.fire_at_iso!r}")
    elif v == "relative_delay":
        if not spec.relative_to_field.strip() and spec.relative_offset_seconds == 0:
            raise ValueError(
                "relative_delay requires relative_to_field or a non-zero relative_offset_seconds"
            )
    elif v == "deadline":
        if not spec.deadline_field.strip() and not spec.fire_at_iso.strip():
            raise ValueError("deadline trigger requires deadline_field (or fire_at_iso)")
    elif v == "business_calendar":
        if not spec.business_calendar_id.strip():
            raise ValueError("business_calendar trigger requires business_calendar_id")
    elif v == "api_poll":
        if not spec.poll_url.strip():
            raise ValueError("api_poll trigger requires poll_url")
        if spec.poll_interval_seconds <= 0:
            raise ValueError("api_poll trigger requires poll_interval_seconds > 0")
    elif v == "rss_feed":
        if not spec.rss_url.strip():
            raise ValueError("rss_feed trigger requires rss_url")
    elif v == "db_row_change":
        if not spec.db_table.strip():
            raise ValueError("db_row_change trigger requires db_table")
    elif v == "file_drop":
        if not spec.file_drop_path.strip():
            raise ValueError("file_drop trigger requires file_drop_path")
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
