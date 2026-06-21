"""Execution memory — stores winning plans and failed approaches across runs.

Winning plans are fed back into the planner prompt to bias toward proven approaches.
Failed approaches are included as negative examples to avoid repeating mistakes.

In production this would be backed by PostgreSQL (execution_memory table).
This in-memory implementation is used in tests.
"""

from __future__ import annotations

from app.tenancy.context import TenantContext


class ExecutionMemory:
    """Per-tenant store of past executions (successful plans and failures)."""

    def __init__(self) -> None:
        # Key: tenant_id → list of memory records
        self._plans: dict[str, list[dict[str, object]]] = {}
        self._failures: dict[str, list[dict[str, object]]] = {}

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
