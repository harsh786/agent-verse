"""Trigger lineage: the loop guard for platform-event triggers (B7).

Six trigger types fire on events the platform itself produces while running
goals: ``goal_completed`` / ``goal_failed`` / ``goal_score_below`` (a goal's
lifecycle), ``hitl_approved`` / ``hitl_rejected`` (a goal's approval gate) and
``memory_created`` (a goal's learning). A goal those triggers start produces the
same kinds of events, so without a guard a trigger re-fires on its own output.

Every goal a trigger creates carries its lineage in ``execution_context``:

* ``trigger_chain_depth`` — 1 for a goal fired by a plain event, the source
  goal's depth + 1 for a goal fired by another trigger-created goal's event;
* ``source_trigger_id`` — the trigger that created it.

The consumers resolve the source goal's lineage (from the event, or from the
goal row for HITL / memory events, which carry only the goal id) and the
dispatcher refuses — and audits — a firing that would exceed
:data:`MAX_CHAIN_DEPTH` or that is the trigger firing on its own goal's event
(unless the trigger sets ``allow_self_trigger``; the depth cap still applies).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# Hard cap on a trigger chain: a goal at this depth never starts another one.
MAX_CHAIN_DEPTH = 10

LINEAGE_TRIGGER_TYPES: frozenset[str] = frozenset(
    {
        "goal_completed",
        "goal_failed",
        "goal_score_below",
        "hitl_approved",
        "hitl_rejected",
        "memory_created",
    }
)


@dataclass(frozen=True)
class GoalLineage:
    """The trigger lineage of one goal (depth 0 = not started by a trigger)."""

    depth: int = 0
    source_trigger_id: str = ""


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def lineage_from_context(ctx: Any) -> GoalLineage:
    """Lineage from a goal's ``execution_context`` (a dict or its JSON text)."""
    if isinstance(ctx, str):
        try:
            ctx = json.loads(ctx)
        except ValueError:
            ctx = {}
    if not isinstance(ctx, dict):
        return GoalLineage()
    return GoalLineage(
        depth=_int(ctx.get("trigger_chain_depth")),
        source_trigger_id=str(ctx.get("source_trigger_id", "") or ""),
    )


async def read_goal_lineage(db_factory: Any, tenant_id: str, goal_id: str) -> GoalLineage | None:
    """The goal row's lineage, or ``None`` when the goal does not exist.

    Tenant-scoped twice (RLS GUC + an explicit ``tenant_id`` predicate), so one
    tenant's event can never read another tenant's goal. A DB error raises: the
    caller must not fire a trigger whose loop guard could not be evaluated.
    """
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    async with db_factory() as session, sqlalchemy_rls_context(session, tenant_id):
        row = (
            await session.execute(
                text(
                    "SELECT execution_context FROM goals "
                    "WHERE id = :g AND tenant_id = :t LIMIT 1"
                ),
                {"g": goal_id, "t": tenant_id},
            )
        ).fetchone()
    if row is None:
        return None
    return lineage_from_context(row[0])


async def source_lineage(dispatcher: Any, tenant_id: str, goal_id: str, data: dict) -> GoalLineage:
    """The lineage of the goal that produced an event.

    Starts from what the event carries and, when the dispatcher can read goal
    rows, takes the deeper depth and fills a missing source trigger from the
    goal row. Errors propagate (fail closed).
    """
    carried = lineage_from_context(data)
    resolver = getattr(dispatcher, "resolve_goal_lineage", None)
    if not goal_id or not callable(resolver):
        return carried
    stored = await resolver(tenant_id, goal_id)
    if not isinstance(stored, GoalLineage):
        return carried
    return GoalLineage(
        depth=max(carried.depth, stored.depth),
        source_trigger_id=carried.source_trigger_id or stored.source_trigger_id,
    )


def chained_payload(data: dict, source: GoalLineage) -> dict:
    """The event payload handed to the dispatcher: the depth of the goal the
    firing would create, and the trigger that created the event's source goal."""
    return {
        **data,
        "trigger_chain_depth": source.depth + 1,
        "source_trigger_id": source.source_trigger_id,
    }


def loop_guard_reason(trigger_type: str, trigger_id: str, spec: Any, payload: Any) -> str | None:
    """Why a platform-event firing must not run (``None`` = it may).

    ``payload`` is the dispatched payload: ``trigger_chain_depth`` is the depth of
    the goal to create, ``source_trigger_id`` the trigger that created the source.
    """
    if trigger_type not in LINEAGE_TRIGGER_TYPES or not isinstance(payload, dict):
        return None
    if _int(payload.get("trigger_chain_depth")) > MAX_CHAIN_DEPTH:
        return "chain_depth_exceeded"
    source_trigger = str(payload.get("source_trigger_id", "") or "")
    if (
        source_trigger
        and trigger_id
        and source_trigger == trigger_id
        and not bool(getattr(spec, "allow_self_trigger", False))
    ):
        return "self_trigger"
    return None


__all__ = [
    "LINEAGE_TRIGGER_TYPES",
    "MAX_CHAIN_DEPTH",
    "GoalLineage",
    "chained_payload",
    "lineage_from_context",
    "loop_guard_reason",
    "read_goal_lineage",
    "source_lineage",
]
