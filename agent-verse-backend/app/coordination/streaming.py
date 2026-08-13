"""Bounded serialization helpers for replayable coordination transports."""

from __future__ import annotations

import json
from typing import Any

from app.coordination.replay import ReplayEvent

_SENSITIVE_KEYS = frozenset(
    {"api_key", "authorization", "password", "private_reasoning", "prompt", "secret", "token"}
)


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else _safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    return value


def public_event(event: ReplayEvent) -> dict[str, Any]:
    """Return a stable, secret-safe event envelope for SSE clients."""
    data = event.model_dump(mode="json")
    data.setdefault("run_id", event.session_id)
    data.setdefault("correlation_id", event.session_id)
    data.setdefault("causation_id", None)
    data.setdefault("producer", "coordination-runtime")
    data.setdefault("classification", "internal")
    data["payload"] = _safe_value(data.get("payload") or {})
    return data


def encode_sse_event(event: ReplayEvent) -> str:
    data = json.dumps(public_event(event), separators=(",", ":"), sort_keys=True)
    return f"id: {event.sequence}\nevent: {event.event_type}\ndata: {data}\n\n"


def encode_heartbeat() -> str:
    return ": heartbeat\n\n"


__all__ = ["encode_heartbeat", "encode_sse_event", "public_event"]
