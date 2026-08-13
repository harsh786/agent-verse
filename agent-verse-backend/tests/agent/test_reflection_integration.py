from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, GoalStatus
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext(tenant_id="reflection-tenant", plan=PlanTier.FREE, api_key_id="key")


def _graph(*responses: str) -> AgentGraph:
    provider = FakeProvider(responses=list(responses) or ["quality issue"])
    return AgentGraph(
        planner=provider,
        executor=provider,
        verifier=provider,
        enable_reflection=True,
        max_iterations=10,
    )


def _failed_state() -> AgentState:
    state = AgentState(goal="repair service", tenant_ctx=TENANT)
    state.verification_success = False
    state.verification_feedback = "verification failed on correctness"
    state.context["verification_retry"] = True
    return state


def test_failed_verification_routes_once_through_reflection() -> None:
    graph = _graph()
    state = _failed_state()
    assert graph._route({"agent_state": state, "tenant_ctx": TENANT, "iteration": 1}) == (
        "reflect"
    )


@pytest.mark.asyncio
async def test_reflection_feedback_is_injected_into_replan() -> None:
    captured: list[str] = []

    class CapturingProvider(FakeProvider):
        async def complete(self, request):
            captured.extend(str(message.content) for message in request.messages)
            return await super().complete(request)

    provider = CapturingProvider(responses=["ROOT_CAUSE: incorrect input", '{"steps": ["retry"]}'])
    graph = AgentGraph(
        planner=provider,
        executor=provider,
        verifier=provider,
        enable_reflection=True,
    )
    state = _failed_state()
    await graph._node_reflect({"agent_state": state, "tenant_ctx": TENANT})
    await graph._node_plan(
        {"agent_state": state, "tenant_ctx": TENANT, "iteration": 1, "rag_context": ""}
    )
    assert any("Reflection identified categories: accuracy" in item for item in captured)
    assert all("incorrect input" not in item for item in captured[2:])


def test_reflection_never_runs_after_success() -> None:
    state = _failed_state()
    state.verification_success = True
    assert _graph()._route(
        {"agent_state": state, "tenant_ctx": TENANT, "iteration": 1}
    ) == "complete"


def test_reflection_limit_terminates_replan() -> None:
    state = _failed_state()
    state.context["reflection_attempts"] = 2
    assert _graph()._route(
        {"agent_state": state, "tenant_ctx": TENANT, "iteration": 2}
    ) == "max_iter"
    assert state.status is GoalStatus.FAILED
    assert state.context["reasoning_evidence"][-1]["status"] == "exhausted"


@pytest.mark.asyncio
async def test_reflection_does_not_replace_original_verification_evidence() -> None:
    private_critique = "ROOT_CAUSE: secret token sk-private; FIX: retry"
    graph = _graph(private_critique)
    graph._emit = MagicMock()
    state = _failed_state()
    original = state.verification_feedback
    await graph._node_reflect({"agent_state": state, "tenant_ctx": TENANT})
    assert state.context["original_verification_evidence"] == original
    assert private_critique not in str(state.context)
    assert "sk-private" not in state.verification_feedback
