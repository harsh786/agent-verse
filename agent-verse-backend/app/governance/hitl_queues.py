"""Derived HITL queue ids (TRG-23).

There is no separate "queue" object: an approval request belongs to

* ``agent:<agent_id>`` — the agent whose goal raised it, and
* ``risk:<tier>``      — its risk tier from the existing risk classification
  (the ``risk_level`` the gate filed it with: ``high`` / ``critical`` / … or a
  tool-risk tier such as ``write_high`` / ``destructive``).

Both ids are published on ``hitl.approved`` / ``hitl.rejected`` events; a
trigger's ``hitl_queue_id`` filter matches either. Any other filter format is
rejected at trigger create / update — it could never match an event.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

AGENT_PREFIX = "agent:"
RISK_PREFIX = "risk:"
# Every tier an approval request is filed with: the gate levels plus the tool
# risk tiers from app.agent.tool_risk.classify_tool_risk.
RISK_TIERS = frozenset(
    {"low", "medium", "high", "critical", "read", "write_low", "write_high", "destructive"}
)
_AGENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def queue_ids(agent_id: str | None, risk_level: str | None) -> list[str]:
    """The queue ids of a request: its agent queue (when known), then its risk queue."""
    ids: list[str] = []
    if agent_id:
        ids.append(f"{AGENT_PREFIX}{agent_id}")
    tier = (risk_level or "").strip().lower()
    if tier:
        ids.append(f"{RISK_PREFIX}{tier}")
    return ids


def queue_id_error(value: str) -> str | None:
    """Why *value* is not a valid ``hitl_queue_id`` filter, or ``None``."""
    if value.startswith(AGENT_PREFIX):
        if _AGENT_ID_RE.fullmatch(value[len(AGENT_PREFIX):]):
            return None
        return f"hitl_queue_id {value!r}: 'agent:' must be followed by an agent id"
    if value.startswith(RISK_PREFIX):
        if value[len(RISK_PREFIX):] in RISK_TIERS:
            return None
        return (
            f"hitl_queue_id {value!r}: unknown risk tier; use one of "
            + ", ".join(f"risk:{t}" for t in sorted(RISK_TIERS))
        )
    return (
        f"hitl_queue_id {value!r} is not a HITL queue; use 'agent:<agent_id>' or "
        "'risk:<tier>' (e.g. risk:high)"
    )


def matches(watch_queue: str, event: Mapping[str, Any]) -> bool:
    """Does a trigger watching *watch_queue* ("" = any) match this HITL event?"""
    if not watch_queue:
        return True
    published = event.get("hitl_queue_ids")
    ids = [str(q) for q in published] if isinstance(published, list) else []
    legacy = event.get("hitl_queue_id")
    if legacy:
        ids.append(str(legacy))
    return watch_queue in ids
