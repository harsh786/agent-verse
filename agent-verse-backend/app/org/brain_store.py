"""BrainDecisionStore — durable audit trail for the autonomous org brain.

Persists one row per tick decision (``app.org.models.OrgBrainDecision`` /
migration ``0128_org_brain_decisions``, table ``org_brain_decisions``) so
brain activity survives restarts and is queryable later: the tick loop calls
:meth:`BrainDecisionStore.record` after each guardrail-evaluated decision, and
the decisions endpoint/UI calls :meth:`BrainDecisionStore.list`.

Tenant isolation is enforced both at the database layer via Row-Level
Security (see ``app/db/rls.py`` — the caller is expected to run inside an
RLS-scoped transaction) and, defense-in-depth, by an explicit ``tenant_id``
filter on every read here, matching the pattern used by the other org_*
stores (e.g. ``app/workflow/run_store.py``).
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import text as sa_text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class BrainDecisionStore:
    """Records and lists org-brain tick decisions in ``org_brain_decisions``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        org_id: str | uuid.UUID,
        tenant_id: str | uuid.UUID,
        tick_id: str,
        kind: str,
        rationale: str,
        target_goal: str,
        action: str,
        guardrail_verdict: str,
        reason: str,
        est_cost_usd: float,
        mission_id: str | uuid.UUID | None = None,
        guardrail_trace: list[dict[str, Any]] | None = None,
    ) -> str:
        """Insert one decision row and return its new id (string UUID).

        ``guardrail_trace`` is the ordered list of per-check results from
        ``app.org.brain_guardrails.evaluate_guardrails`` (already plain dicts,
        e.g. via ``dataclasses.asdict``), persisted as JSONB for the Brain
        Feed v2 UI to render the full 8-check SENSE→DECIDE→GUARD→ACT trace.
        """
        decision_id = str(uuid.uuid4())
        await self._session.execute(
            sa_text(
                "INSERT INTO org_brain_decisions "
                "(id, org_id, tenant_id, tick_id, kind, rationale, target_goal, "
                " action, guardrail_verdict, reason, est_cost_usd, mission_id, guardrail_trace) "
                "VALUES (CAST(:id AS uuid), CAST(:org_id AS uuid), CAST(:tenant_id AS uuid), "
                " :tick_id, :kind, :rationale, :target_goal, :action, :guardrail_verdict, "
                " :reason, :est_cost_usd, CAST(:mission_id AS uuid), "
                " CAST(:guardrail_trace AS jsonb))"
            ),
            {
                "id": decision_id,
                "org_id": str(org_id),
                "tenant_id": str(tenant_id),
                "tick_id": tick_id,
                "kind": kind,
                "rationale": rationale,
                "target_goal": target_goal,
                "action": action,
                "guardrail_verdict": guardrail_verdict,
                "reason": reason,
                "est_cost_usd": float(est_cost_usd),
                "mission_id": str(mission_id) if mission_id is not None else None,
                "guardrail_trace": (
                    json.dumps(guardrail_trace) if guardrail_trace is not None else None
                ),
            },
        )
        return decision_id

    async def list(
        self,
        org_id: str | uuid.UUID,
        tenant_id: str | uuid.UUID,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` most recent decisions for ``org_id``, newest first."""
        result = await self._session.execute(
            sa_text(
                "SELECT id, tick_id, kind, rationale, target_goal, action, "
                " guardrail_verdict, reason, est_cost_usd, mission_id, guardrail_trace, created_at "
                "FROM org_brain_decisions "
                "WHERE org_id = CAST(:org_id AS uuid) "
                " AND tenant_id = CAST(:tenant_id AS uuid) "
                "ORDER BY created_at DESC LIMIT :limit"
            ),
            {"org_id": str(org_id), "tenant_id": str(tenant_id), "limit": limit},
        )
        rows = result.mappings().all()
        return [dict(row) for row in rows]
