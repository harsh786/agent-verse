"""GET /observability/goals/{id}/trace — the Run Inspector read API."""

from __future__ import annotations

import pytest

from app.api.observability import get_goal_trace
from app.observability import tracing
from app.observability.run_timeline import InMemoryRunTimelineStore
from app.tenancy.context import PlanTier, TenantContext


class _Req:
    def __init__(self, tenant: TenantContext | None) -> None:
        self.state = type("S", (), {"tenant": tenant})()


@pytest.fixture
def store() -> InMemoryRunTimelineStore:
    s = InMemoryRunTimelineStore()
    original = tracing._run_timeline_store
    tracing._run_timeline_store = s
    try:
        yield s
    finally:
        tracing._run_timeline_store = original


async def test_returns_tenant_scoped_timeline_with_summary(store: InMemoryRunTimelineStore) -> None:
    store.append("acme", "g1", {"name": "gen_ai.planner", "cost_usd": 0.001,
                                 "input_tokens": 10, "output_tokens": 4})
    store.append("acme", "g1", {"name": "agentverse.tool.call", "tool": "jira.search"})
    store.append("other", "g1", {"name": "gen_ai.planner", "cost_usd": 9.0})  # other tenant

    ctx = TenantContext(tenant_id="acme", plan=PlanTier.FREE, api_key_id="k")
    resp = await get_goal_trace("g1", _Req(ctx))

    assert resp["goal_id"] == "g1"
    assert len(resp["entries"]) == 2  # only acme's entries
    assert resp["summary"]["steps"] == 2
    assert resp["summary"]["generations"] == 1
    assert resp["summary"]["total_cost_usd"] == 0.001  # other tenant's 9.0 excluded
    assert resp["summary"]["total_output_tokens"] == 4


async def test_unauthorized_without_tenant(store: InMemoryRunTimelineStore) -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await get_goal_trace("g1", _Req(None))
