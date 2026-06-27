"""Execution memory — stores winning plans and failed approaches across runs.

Winning plans are fed back into the planner prompt to bias toward proven approaches.
Failed approaches are included as negative examples to avoid repeating mistakes.

In production this would be backed by PostgreSQL (execution_memory table).
This in-memory implementation is used in tests.
"""

from __future__ import annotations

from typing import Any

from app.tenancy.context import TenantContext


class ExecutionMemory:
    """Per-tenant store of past executions (successful plans and failures)."""

    def __init__(self) -> None:
        # Key: tenant_id → list of memory records
        self._plans: dict[str, list[dict[str, object]]] = {}
        self._failures: dict[str, list[dict[str, object]]] = {}
        # Flat execution log used by the Memory REST API
        self._memories: dict[str, list[dict[str, object]]] = {}

    def record(
        self,
        *,
        goal: str,
        plan: list[str],
        tenant_ctx: TenantContext,
    ) -> None:
        self._plans.setdefault(tenant_ctx.tenant_id, []).append(
            {"goal": goal, "plan": plan}
        )

    def recall(
        self,
        *,
        goal_hint: str,
        tenant_ctx: TenantContext,
        top_k: int = 5,
    ) -> list[dict[str, object]]:
        hint = goal_hint.lower()
        matches = [
            m
            for m in self._plans.get(tenant_ctx.tenant_id, [])
            if hint in str(m["goal"]).lower()
        ]
        return matches[:top_k]

    def record_failure(
        self,
        *,
        goal: str,
        failed_step: str,
        error: str,
        tenant_ctx: TenantContext,
    ) -> None:
        self._failures.setdefault(tenant_ctx.tenant_id, []).append(
            {"goal": goal, "failed_step": failed_step, "error": error}
        )

    def recall_failures(
        self,
        *,
        goal_hint: str,
        tenant_ctx: TenantContext,
        top_k: int = 5,
    ) -> list[dict[str, object]]:
        hint = goal_hint.lower()
        matches = [
            m
            for m in self._failures.get(tenant_ctx.tenant_id, [])
            if hint in str(m["goal"]).lower()
        ]
        return matches[:top_k]

    async def record_async(
        self,
        *,
        goal_text: str,
        plan: list[str],
        success: bool,
        tenant_ctx: TenantContext,
        db: object = None,
    ) -> None:
        """Record to both in-memory dict and PostgreSQL."""
        from datetime import UTC, datetime
        tid = tenant_ctx.tenant_id
        entry: dict[str, object] = {
            "goal_text": goal_text,
            "plan": plan,
            "success": success,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        self._memories.setdefault(tid, []).append(entry)
        # Keep only last 100 in memory per tenant
        if len(self._memories[tid]) > 100:
            self._memories[tid] = self._memories[tid][-100:]

        if db is None:
            return
        try:
            import json
            import uuid

            from sqlalchemy import text
            async with db() as session, session.begin():
                await session.execute(
                    text("""INSERT INTO execution_memory
                        (id, tenant_id, goal_text, plan, success, created_at)
                        VALUES (:id, :tid, :goal, :plan, :success, NOW())"""),
                    {"id": uuid.uuid4().hex, "tid": tid,
                     "goal": goal_text[:500], "plan": json.dumps(plan), "success": success}
                )
        except Exception as exc:
            from app.observability.logging import get_logger
            get_logger(__name__).warning("execution_memory_db_write_failed", error=str(exc))

    async def recall_async(
        self,
        goal_hint: str,
        *,
        tenant_id: str,
        db: Any = None,
        limit: int = 3,
    ) -> list[dict]:
        """Recall relevant execution plans from DB for a given goal.

        Falls back to in-memory search when ``db`` is None or DB query fails.
        """
        if db is None:
            # In-memory fallback: search _plans by keyword match
            hint_lower = goal_hint.lower()
            results: list[dict] = []
            for m in self._plans.get(tenant_id, []):
                goal_str = str(m.get("goal", m.get("goal_text", "")))
                if any(word in goal_str.lower() for word in hint_lower.split()[:5]):
                    results.append({
                        "goal": goal_str,
                        "plan": m.get("plan", []) if isinstance(m.get("plan"), list) else [],
                        "success": m.get("success", True),
                    })
                    if len(results) >= limit:
                        break
            return results

        try:
            from sqlalchemy import text
            async with db() as session:
                rows = (await session.execute(
                    text("""
                        SELECT goal_text, plan, success FROM execution_memory
                        WHERE tenant_id = :tid AND success = TRUE
                        ORDER BY created_at DESC LIMIT :lim
                    """),
                    {"tid": tenant_id, "lim": limit * 3}
                )).fetchall()

            # Filter by keyword relevance
            hint_lower = goal_hint.lower()
            filtered: list[dict] = []
            for row in rows:
                goal_text, plan, success = row
                if any(word in (goal_text or "").lower() for word in hint_lower.split()[:5]):
                    filtered.append({
                        "goal": goal_text,
                        "plan": plan if isinstance(plan, list) else [],
                        "success": success,
                    })
                    if len(filtered) >= limit:
                        break
            return filtered
        except Exception:
            # DB failed — fall back to in-memory
            hint_lower = goal_hint.lower()
            fallback: list[dict] = []
            for m in self._plans.get(tenant_id, []):
                goal_str = str(m.get("goal", m.get("goal_text", "")))
                if any(word in goal_str.lower() for word in hint_lower.split()[:5]):
                    fallback.append({
                        "goal": goal_str,
                        "plan": m.get("plan", []) if isinstance(m.get("plan"), list) else [],
                        "success": m.get("success", True),
                    })
                    if len(fallback) >= limit:
                        break
            return fallback
