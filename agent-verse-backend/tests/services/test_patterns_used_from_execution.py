"""CORE-21: goals.patterns_used reflects what actually ran, not what was requested."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any

from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="t-pu", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _Session:
    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        self.statements.append((str(stmt), dict(params or {})))
        return None

    async def flush(self) -> None:
        return None

    @asynccontextmanager
    async def begin(self) -> Any:
        yield self


async def test_legacy_goal_profile_columns_claim_no_patterns() -> None:
    data = await GoalService()._build_runtime_profile(
        "summarize the quarterly report", goal_id="g-1", tenant_ctx=TENANT
    )
    assert data["context"]["strategy_runtime_path"] == "legacy"
    assert data["columns"]["patterns_used"] == []


async def test_strategy_execution_sets_patterns_used_to_what_ran() -> None:
    session = _Session()

    @asynccontextmanager
    async def factory() -> Any:
        yield session

    svc = GoalService(db_session_factory=factory)
    await svc._db_merge_context_key(
        "g-1",
        TENANT.tenant_id,
        "strategy_execution",
        {"driver": "agent_graph", "patterns": ["react"]},
    )

    updates = [(sql, p) for sql, p in session.statements if "patterns_used" in sql]
    assert len(updates) == 1
    assert json.loads(updates[0][1]["p"]) == ["react"]


async def test_other_context_keys_leave_patterns_used_alone() -> None:
    session = _Session()

    @asynccontextmanager
    async def factory() -> Any:
        yield session

    await GoalService(db_session_factory=factory)._db_merge_context_key(
        "g-1", TENANT.tenant_id, "provider_warning", "x"
    )
    assert not [s for s, _ in session.statements if "patterns_used" in s]
