"""Goal-tree sub-agents inherit grant enforcement and the parent's agent identity.

Regression: the sub-graph factory omitted grant_store / enforce_grants (and the
agent id grants are keyed on), so a plan large enough to fan out to sub-agents
ran tools with no grant check at all.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k")


@pytest.mark.asyncio
async def test_subgraph_factory_carries_grant_enforcement() -> None:
    store = object()
    fake = FakeProvider()
    graph = AgentGraph(
        planner=fake, executor=fake, verifier=fake,
        enable_goal_tree=True, goal_tree_threshold=2,
        grant_store=store, enforce_grants=True,
    )
    graph._agent_id = "agent-7"
    captured: dict[str, Any] = {}

    async def _fake_tree(goal: str, **kwargs: Any) -> list[Any]:
        captured["sub"] = kwargs["graph_factory"]()
        return []

    state = AgentState(goal="big goal", tenant_ctx=T)
    state.plan = ["a", "b", "c"]
    with patch("app.agent.goal_tree.execute_goal_tree", _fake_tree):
        try:
            await graph._node_execute({"agent_state": state, "tenant_ctx": T, "plan": state.plan})
        except Exception:
            pass  # only the factory wiring matters here
    sub = captured["sub"]
    assert sub._grant_store is store
    assert sub._enforce_grants is True
    assert sub._agent_id == "agent-7"
