"""GOAL-STRATEGIES: goals selecting a coordination pattern run it end to end."""

from __future__ import annotations

from typing import Any

import pytest

from app.governance.hitl import ApprovalStatus
from app.orchestration.execution_drivers import (
    COORDINATION_PATTERN_STRATEGIES,
    ExecutionDriver,
    goal_execution_driver,
)
from app.orchestration.strategy_executor import default_distributed_admission
from app.orchestration.strategy_registry import build_default_registry
from app.providers.fake import FakeProvider
from app.providers.guarded_completion import set_platform_cost_services
from tests.coordination.goal_strategy_support import run_goal
from tests.coordination.pattern_run_support import TENANT, ScriptedProvider, pattern_state

EXPECTED_ANSWER = {
    "magentic": "FINAL REPORT",
    "mixture_of_agents": "AGGREGATED ANSWER",
    "camel": "CAMEL SOLUTION",
    "generative_agents": "GENERATIVE SUMMARY",
    "decentralized_swarm": "SWARM ANSWER",
    "market_auction": "DELIVERED WORK",
}


def test_coordination_patterns_are_goal_executable_and_admitted() -> None:
    registry = build_default_registry()
    for strategy_id in COORDINATION_PATTERN_STRATEGIES:
        capability = registry.resolve(strategy_id).capability
        assert goal_execution_driver(capability) is ExecutionDriver.STRATEGY_RUNNER
        request: Any = type("R", (), {"strategy_id": strategy_id})()
        assert default_distributed_admission(request) == (True, "admitted")


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_id", sorted(EXPECTED_ANSWER))
async def test_goal_runs_pattern_on_a_goal_linked_session(strategy_id: str) -> None:
    provider = ScriptedProvider()
    state = pattern_state(provider)
    result, events = await run_goal(
        state,
        strategy_id,
        goal="Write a short market report",
        goal_id=f"goal-{strategy_id}",
        tenant_ctx=TENANT,
        provider=provider,
    )
    assert result["terminal_state"] == "succeeded", events[-1]
    assert result["answer"] == EXPECTED_ANSWER[strategy_id]
    complete = events[-1]
    assert complete["type"] == "goal_complete" and complete["strategy_id"] == strategy_id
    session_event = next(e for e in events if e["type"] == "coordination_session")
    progress = [e for e in events if e["type"] == "coordination_progress"]
    assert progress, "pattern steps must stream as goal events"
    assert all(e["session_id"] == session_event["session_id"] for e in progress)
    session = await state.coordination_service.get_session(TENANT, session_event["session_id"])
    assert session.state == "completed"


@pytest.mark.asyncio
async def test_goal_retry_reuses_the_goal_session_and_run() -> None:
    provider = ScriptedProvider()
    state = pattern_state(provider)
    first, events = await run_goal(
        state,
        "camel",
        goal="Plan the offsite",
        goal_id="goal-retry",
        tenant_ctx=TENANT,
        provider=provider,
    )
    session_id = next(e for e in events if e["type"] == "coordination_session")["session_id"]
    calls = len(provider.prompts)
    # The session is completed now, so a retried goal fails closed instead of
    # silently re-running on a closed session.
    second, events2 = await run_goal(
        state,
        "camel",
        goal="Plan the offsite",
        goal_id="goal-retry",
        tenant_ctx=TENANT,
        provider=provider,
    )
    assert first["terminal_state"] == "succeeded"
    assert second["terminal_state"] == "failed"
    assert "coordination_session_closed" in events2[-1]["reason"]
    assert len(provider.prompts) == calls
    assert session_id


@pytest.mark.asyncio
async def test_goal_budget_denial_fails_the_goal() -> None:
    class DenyingController:
        async def check_and_record(self, **_: Any) -> bool:
            return False

    set_platform_cost_services(lambda: (DenyingController(), None))
    try:
        provider = ScriptedProvider()
        state = pattern_state(provider)
        result, events = await run_goal(
            state,
            "camel",
            goal="Summarise",
            goal_id="goal-budget",
            tenant_ctx=TENANT,
            provider=provider,
        )
    finally:
        set_platform_cost_services(None)
    assert result["terminal_state"] == "failed"
    assert "pattern_budget_exceeded" in events[-1]["reason"]


@pytest.mark.asyncio
async def test_goal_limits_bound_the_pattern() -> None:
    provider = ScriptedProvider()
    state = pattern_state(provider)
    result, events = await run_goal(
        state,
        "decentralized_swarm",
        goal="Map vendors",
        goal_id="goal-limit",
        tenant_ctx=TENANT,
        provider=provider,
        calls=2,
    )
    assert result["terminal_state"] == "failed"
    assert "pattern_llm_call_limit" in events[-1]["reason"]
    assert len(provider.prompts) == 2


class FakeGateway:
    def __init__(self, status: ApprovalStatus) -> None:
        self.status = status
        self.requests: list[dict[str, Any]] = []

    async def request_approval_async(self, **kwargs: Any) -> str:
        self.requests.append(kwargs)
        return f"req-{len(self.requests)}"

    async def wait_for_approval(self, request_id: str, **_: Any) -> ApprovalStatus:
        return self.status


@pytest.mark.asyncio
async def test_high_risk_goal_waits_for_approval() -> None:
    provider = ScriptedProvider()
    state = pattern_state(provider)
    state.hitl_gateway = FakeGateway(ApprovalStatus.APPROVED)
    result, events = await run_goal(
        state,
        "camel",
        goal="Plan how to deploy the release to production",
        goal_id="goal-hitl-ok",
        tenant_ctx=TENANT,
        provider=provider,
    )
    types = [e["type"] for e in events]
    assert types.index("waiting_approval") < types.index("approval_granted")
    assert result["terminal_state"] == "succeeded"


@pytest.mark.asyncio
async def test_rejected_or_ungated_high_risk_goal_never_runs() -> None:
    provider = ScriptedProvider()
    state = pattern_state(provider)
    state.hitl_gateway = FakeGateway(ApprovalStatus.REJECTED)
    result, events = await run_goal(
        state,
        "camel",
        goal="Delete the production database",
        goal_id="goal-hitl-no",
        tenant_ctx=TENANT,
        provider=provider,
    )
    assert result["terminal_state"] == "failed"
    assert "approval_rejected" in events[-1]["reason"]
    assert provider.prompts == []
    state.hitl_gateway = None
    result, events = await run_goal(
        state,
        "camel",
        goal="Delete the production database",
        goal_id="goal-hitl-none",
        tenant_ctx=TENANT,
        provider=provider,
    )
    assert "approval_unavailable" in events[-1]["reason"]
    assert provider.prompts == []


@pytest.mark.asyncio
async def test_magentic_human_review_goes_through_the_goal_gate() -> None:
    provider = ScriptedProvider(magentic_completes=False)
    state = pattern_state(provider)

    class ApproveAndFix(FakeGateway):
        async def wait_for_approval(self, request_id: str, **_: Any) -> ApprovalStatus:
            provider.magentic_completes = True  # the human adds what was missing
            return ApprovalStatus.APPROVED

    state.hitl_gateway = ApproveAndFix(ApprovalStatus.APPROVED)
    result, events = await run_goal(
        state,
        "magentic",
        goal="Write the report",
        goal_id="goal-magentic-review",
        tenant_ctx=TENANT,
        provider=provider,
    )
    assert result["terminal_state"] == "succeeded", events[-1]
    assert result["answer"] == "FINAL REPORT"
    assert any(e["type"] == "waiting_approval" for e in events)


@pytest.mark.asyncio
async def test_fake_provider_goal_fails_closed() -> None:
    state = pattern_state(None)
    result, events = await run_goal(
        state,
        "camel",
        goal="Summarise",
        goal_id="goal-fake",
        tenant_ctx=TENANT,
        provider=FakeProvider(responses=["canned"]),
    )
    assert result["terminal_state"] == "failed"
    assert "pattern_unavailable" in events[-1]["reason"]
