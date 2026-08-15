"""AgentGraph threads one goal identity into retrieval cost control."""

from unittest.mock import AsyncMock, patch

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext


@pytest.mark.asyncio
async def test_execute_step_threads_goal_id_to_rag_cost_execution() -> None:
    tenant = TenantContext("tenant-cost", PlanTier.ENTERPRISE, "key")
    graph = AgentGraph(
        planner=FakeProvider(responses=["step 1"]),
        executor=FakeProvider(responses=["step output"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "done"}']),
    )
    agent_state = AgentState(goal="goal with retrieval", tenant_ctx=tenant)

    with patch(
        "app.agent.nodes.executor_mixin.smart_context_fetch",
        AsyncMock(return_value=""),
    ) as fetch:
        await graph._execute_step("step 1", agent_state, tenant)

    assert fetch.await_args.kwargs["execution_id"] == agent_state.goal_id
