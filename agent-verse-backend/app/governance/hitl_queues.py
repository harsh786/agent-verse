"""Derived HITL queue ids (TRG-23).

There is no separate "queue" object. A goal approval request belongs to

* ``agent:<agent_id>`` — the agent whose goal raised it, and
* ``risk:<tier>``      — its risk tier from the existing risk classification
  (the ``risk_level`` the gate filed it with: ``high`` / ``critical`` / … or a
  tool-risk tier such as ``write_high`` / ``destructive``).

A workflow approval gate (``HITLWorkflowGateway``) has no agent and no risk
classification, so it belongs to

* ``workflow:<workflow_id>`` — the workflow whose run raised it, and
* ``risk:<priority>``        — its priority (``critical`` / ``high`` /
  ``medium`` / ``low``, the same four-level scale as the gate risk levels).

These ids are published on ``hitl.approved`` / ``hitl.rejected`` events for
both kinds of approval; a trigger's ``hitl_queue_id`` filter matches any of
them. Workflow events used to carry no queue at all, so a queue-filtered HITL
trigger never fired for a workflow gate (B7 live open item 2). Any other filter
format is rejected at trigger create / update — it could never match an event.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

AGENT_PREFIX = "agent:"
RISK_PREFIX = "risk:"
WORKFLOW_PREFIX = "workflow:"
# Every tier an approval request is filed with: the gate levels plus the tool
# risk tiers from app.agent.tool_risk.classify_tool_risk.
RISK_TIERS = frozenset(
    {"low", "medium", "high", "critical", "read", "write_low", "write_high", "destructive"}
)
_AGENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
# Workflow ids are generated hex/uuid strings; same shape as agent ids.
_WORKFLOW_ID_RE = _AGENT_ID_RE


def queue_ids(agent_id: str | None, risk_level: str | None) -> list[str]:
    """The queue ids of a request: its agent queue (when known), then its risk queue."""
    ids: list[str] = []
    if agent_id:
        ids.append(f"{AGENT_PREFIX}{agent_id}")
    tier = (risk_level or "").strip().lower()
    if tier:
        ids.append(f"{RISK_PREFIX}{tier}")
    return ids


def workflow_queue_ids(workflow_id: str | None, priority: str | None) -> list[str]:
    """The queue ids of a workflow approval gate: its workflow, then its priority tier."""
    ids: list[str] = []
    if workflow_id:
        ids.append(f"{WORKFLOW_PREFIX}{workflow_id}")
    tier = (priority or "").strip().lower()
    if tier in RISK_TIERS:
        ids.append(f"{RISK_PREFIX}{tier}")
    return ids


def queue_id_error(value: str) -> str | None:
    """Why *value* is not a valid ``hitl_queue_id`` filter, or ``None``."""
    if value.startswith(AGENT_PREFIX):
        if _AGENT_ID_RE.fullmatch(value[len(AGENT_PREFIX):]):
            return None
        return f"hitl_queue_id {value!r}: 'agent:' must be followed by an agent id"
    if value.startswith(WORKFLOW_PREFIX):
        if _WORKFLOW_ID_RE.fullmatch(value[len(WORKFLOW_PREFIX):]):
            return None
        return f"hitl_queue_id {value!r}: 'workflow:' must be followed by a workflow id"
    if value.startswith(RISK_PREFIX):
        if value[len(RISK_PREFIX):] in RISK_TIERS:
            return None
        return (
            f"hitl_queue_id {value!r}: unknown risk tier; use one of "
            + ", ".join(f"risk:{t}" for t in sorted(RISK_TIERS))
        )
    return (
        f"hitl_queue_id {value!r} is not a HITL queue; use 'agent:<agent_id>', "
        "'workflow:<workflow_id>' or 'risk:<tier>' (e.g. risk:high)"
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
