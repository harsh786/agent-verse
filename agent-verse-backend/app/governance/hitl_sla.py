"""HITL approval SLA stats + enforcement on ``approval_requests``.

``hitl_approval_requests`` (migration 0056) was never written by application
code — the HITL gateway persists to ``approval_requests`` — so SLA stats read an
always-empty table and ``enforce_hitl_sla`` never had anything to act on. Both now
run on ``approval_requests`` joined to the tenant's active ``approval_sla_configs``
row for the request's risk level:

* SLA deadline = ``created_at + response_sla_minutes`` (falls back to the
  request's own ``expires_at`` when the tenant has no SLA config for that risk).
* A pending request past its response SLA is escalated exactly once
  (``escalated_at``).
* A pending request past ``timeout_minutes`` with ``auto_deny_on_timeout`` is
  rejected (``approver = 'system:sla'``) and its waiter is released via the
  same Redis resolution list the gateway uses.
* ``auto_approve_on_timeout`` is deliberately NOT honoured: approving a gated
  high-risk action because nobody looked at it is fail-open. Such requests are
  escalated instead, and the refusal is logged.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text

from app.observability.logging import get_logger

logger = get_logger(__name__)

# One active SLA config per (tenant, risk_level) — newest wins if several exist.
_SLA_JOIN = """
    LEFT JOIN LATERAL (
        SELECT sc.response_sla_minutes, sc.timeout_minutes,
               sc.auto_deny_on_timeout, sc.auto_approve_on_timeout
        FROM approval_sla_configs sc
        WHERE sc.tenant_id = ar.tenant_id
          AND sc.risk_level = ar.risk_level
          AND sc.is_active
        ORDER BY sc.updated_at DESC
        LIMIT 1
    ) sla ON TRUE
"""

_DEADLINE = (
    "COALESCE(ar.created_at + make_interval(mins => sla.response_sla_minutes), ar.expires_at)"
)

SLA_STATS_SQL = f"""
    SELECT
        COUNT(*) FILTER (WHERE ar.status = 'pending') AS pending,
        COUNT(*) FILTER (WHERE ar.status = 'approved') AS approved,
        COUNT(*) FILTER (WHERE ar.status = 'rejected') AS rejected,
        COUNT(*) FILTER (WHERE ar.status IN ('timed_out', 'expired')) AS timed_out,
        COUNT(*) FILTER (WHERE ar.escalated_at IS NOT NULL) AS escalated,
        COUNT(*) FILTER (
            WHERE ar.resolved_at IS NOT NULL AND {_DEADLINE} IS NOT NULL
              AND ar.resolved_at <= {_DEADLINE}
        ) AS within_sla,
        COUNT(*) FILTER (
            WHERE {_DEADLINE} IS NOT NULL
              AND COALESCE(ar.resolved_at, NOW()) > {_DEADLINE}
        ) AS breached_sla,
        AVG(EXTRACT(EPOCH FROM (ar.resolved_at - ar.created_at)))
            FILTER (WHERE ar.resolved_at IS NOT NULL) AS avg_resolution_seconds
    FROM approval_requests ar
    {_SLA_JOIN}
    WHERE ar.tenant_id = :tid
"""


async def compute_sla_stats(session: Any, tenant_id: str) -> dict[str, Any]:
    """SLA stats for one tenant. Caller runs this inside the tenant's RLS context."""
    row = (await session.execute(text(SLA_STATS_SQL), {"tid": tenant_id})).fetchone()
    if row is None:
        row = (0, 0, 0, 0, 0, 0, 0, None)
    return {
        "pending": int(row[0] or 0),
        "approved": int(row[1] or 0),
        "rejected": int(row[2] or 0),
        # Back-compat key: the old response called rejections "denied".
        "denied": int(row[2] or 0),
        "timed_out": int(row[3] or 0),
        "escalated": int(row[4] or 0),
        "within_sla": int(row[5] or 0),
        "breached_sla": int(row[6] or 0),
        "avg_resolution_seconds": float(row[7]) if row[7] is not None else None,
    }


_AUTO_DENY_SQL = f"""
    WITH due AS (
        SELECT ar.id
        FROM approval_requests ar
        {_SLA_JOIN}
        WHERE ar.status = 'pending'
          AND sla.auto_deny_on_timeout
          AND ar.created_at + make_interval(mins => sla.timeout_minutes) < NOW()
        ORDER BY ar.created_at
        LIMIT :lim
        FOR UPDATE OF ar SKIP LOCKED
    )
    UPDATE approval_requests AS ar
    SET status = 'rejected', approver = 'system:sla',
        note = 'Auto-denied: approval SLA timeout', resolved_at = NOW(),
        escalated_at = COALESCE(ar.escalated_at, NOW())
    FROM due
    WHERE ar.id = due.id AND ar.status = 'pending'
    RETURNING ar.id, ar.tenant_id
"""

_ESCALATE_SQL = f"""
    WITH due AS (
        SELECT ar.id, COALESCE(sla.auto_approve_on_timeout, FALSE) AS wanted_auto_approve
        FROM approval_requests ar
        {_SLA_JOIN}
        WHERE ar.status = 'pending'
          AND ar.escalated_at IS NULL
          AND {_DEADLINE} IS NOT NULL
          AND {_DEADLINE} < NOW()
        ORDER BY ar.created_at
        LIMIT :lim
        FOR UPDATE OF ar SKIP LOCKED
    )
    UPDATE approval_requests AS ar
    SET escalated_at = NOW()
    FROM due
    WHERE ar.id = due.id AND ar.status = 'pending' AND ar.escalated_at IS NULL
    RETURNING ar.id, ar.tenant_id, due.wanted_auto_approve
"""


async def enforce_sla(session: Any, *, redis: Any = None, limit: int = 500) -> dict[str, Any]:
    """Auto-deny timed-out requests and escalate SLA breaches (cross-tenant).

    Must run in a maintenance (RLS-bypassing) session inside a transaction; the
    caller commits. ``redis`` (optional) releases waiters on auto-denied requests.
    """
    denied = (await session.execute(text(_AUTO_DENY_SQL), {"lim": limit})).fetchall()
    escalated = (await session.execute(text(_ESCALATE_SQL), {"lim": limit})).fetchall()

    refused_auto_approve = [str(r[0]) for r in escalated if len(r) > 2 and r[2]]
    if refused_auto_approve:
        logger.warning(
            "hitl_sla_auto_approve_refused",
            count=len(refused_auto_approve),
            request_ids=refused_auto_approve[:20],
            reason="auto-approve on timeout is fail-open; escalated instead",
        )
    for r in escalated:
        logger.warning("hitl_sla_escalated", request_id=str(r[0]), tenant_id=str(r[1]))

    released = 0
    if redis is not None:
        payload = json.dumps(
            {
                "action": "rejected",
                "approver": "system:sla",
                "note": "Auto-denied: approval SLA timeout",
            }
        )
        for r in denied:
            key = f"hitl_result:{r[0]}"
            try:
                await redis.rpush(key, payload)
                await redis.expire(key, 86400)
                released += 1
            except Exception as exc:
                # The DB row is already rejected (authoritative); a waiter that
                # misses this push still times out and re-reads a terminal state.
                logger.warning("hitl_sla_release_failed", request_id=str(r[0]), error=str(exc))

    return {
        "auto_denied": len(denied),
        "escalated": len(escalated),
        "auto_approve_refused": len(refused_auto_approve),
        "waiters_released": released,
    }


__all__ = ["SLA_STATS_SQL", "compute_sla_stats", "enforce_sla"]
