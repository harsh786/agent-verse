"""Which goals used which connector (``goal_connector_usage``, MCPREG-03).

Written by ``MCPClient.call_tool`` when a goal's tool call reaches a connector
(the running goal comes from the decision charge scope every goal run enters),
read by ``GET /connectors/{id}/usage`` with an exact, indexed lookup.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from app.db.rls import sqlalchemy_rls_context

# Per-process de-duplication of (tenant, connector, goal) already recorded: an
# optimisation only (the insert is idempotent), bounded so it cannot grow.
_SEEN: OrderedDict[tuple[str, str, str], None] = OrderedDict()
_SEEN_MAX = 10_000


def current_goal_id() -> str | None:
    """The goal whose run is executing this code, if any."""
    from app.providers import guarded_completion

    scope = guarded_completion._scope.get()
    goal_id = getattr(getattr(scope, "agent_state", None), "goal_id", None)
    return str(goal_id) if goal_id else None


async def record_goal_connector_usage(
    db_factory: Any, tenant_id: str, connector_id: str, goal_id: str
) -> None:
    """Idempotently record that ``goal_id`` used ``connector_id`` (raises on DB error)."""
    key = (tenant_id, connector_id, goal_id)
    if key in _SEEN:
        return
    from sqlalchemy import text

    async with db_factory() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
        await s.execute(
            text(
                "INSERT INTO goal_connector_usage (tenant_id, connector_id, goal_id) "
                "VALUES (:t, :c, :g) ON CONFLICT DO NOTHING"
            ),
            {"t": tenant_id, "c": connector_id[:255], "g": goal_id[:64]},
        )
    _SEEN[key] = None
    while len(_SEEN) > _SEEN_MAX:
        _SEEN.popitem(last=False)


async def connector_usage(
    db_factory: Any, tenant_id: str, connector_id: str, *, limit: int
) -> dict[str, Any]:
    """Goals (newest use first) that used exactly ``connector_id``; raises on DB error."""
    from sqlalchemy import text

    params = {"tid": tenant_id, "cid": connector_id, "limit": int(limit)}
    async with db_factory() as s, s.begin(), sqlalchemy_rls_context(s, tenant_id):
        rows = (
            await s.execute(
                text(
                    "SELECT g.id, g.goal_text, g.status, g.created_at, "
                    # goals has no cost column: the page's spend from the ledger
                    # (indexed by goal_id; at most ``limit`` goals).
                    "(SELECT COALESCE(SUM(c.cost_usd), 0) FROM cost_ledger c "
                    "WHERE c.tenant_id = g.tenant_id AND c.goal_id = g.id) "
                    "FROM goal_connector_usage u JOIN goals g "
                    "ON g.id = u.goal_id AND g.tenant_id = u.tenant_id "
                    "WHERE u.tenant_id = :tid AND u.connector_id = :cid "
                    "ORDER BY u.first_used_at DESC LIMIT :limit"
                ),
                params,
            )
        ).fetchall()
        count_row = (
            await s.execute(
                text(
                    "SELECT COUNT(*), "
                    "COALESCE(SUM(CASE WHEN g.status = 'complete' THEN 1 ELSE 0 END), 0) "
                    "FROM goal_connector_usage u JOIN goals g "
                    "ON g.id = u.goal_id AND g.tenant_id = u.tenant_id "
                    "WHERE u.tenant_id = :tid AND u.connector_id = :cid"
                ),
                params,
            )
        ).fetchone()
    goals = [
        {
            "id": str(r[0]),
            "goal": r[1],
            "status": r[2],
            "created_at": r[3].isoformat() if r[3] else None,
            "cost_usd": float(r[4] or 0),
        }
        for r in rows or []
    ]
    total = int(count_row[0] or 0) if count_row else 0
    success = int(count_row[1] or 0) if count_row else 0
    return {"goals": goals, "total": total, "success_count": success}


__all__ = ["connector_usage", "current_goal_id", "record_goal_connector_usage"]
