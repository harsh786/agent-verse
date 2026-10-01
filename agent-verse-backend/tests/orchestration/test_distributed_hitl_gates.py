"""HITL-DISTRIBUTED-GATES: supervisor / goal_tree / debate wait for a saved approval.

Voyager and the coordination patterns already required a persisted human
approval for high-risk goals; supervisor, goal_tree and debate ran them
straight through. Now, in the executor used by both the API process and the
worker loop:

* high-risk goal text -> approval before anything runs (all three);
* high-risk decomposed sub-tasks -> approval before any child runs
  (supervisor / goal_tree);
* approved -> the run continues; rejected / timed out / no gateway -> the goal
  fails with that reason (approval_rejected / approval_timed_out /
  approval_unavailable); waiting/granted/denied reach the goal's event sink.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.governance.hitl import ApprovalStatus
from app.orchestration.strategy_context_store import StrategyGoalContext, StrategyGoalContextStore
from app.orchestration.strategy_contracts import (
    ExecutionTerminalState,
    PatternLimits,
    StrategyExecutionRequest,
)
from app.orchestration.strategy_executor import (
    DistributedStrategyExecutor,
    default_distributed_admission,
)
from app.orchestration.strategy_registry import build_default_registry
from app.orchestration.strategy_runner import StrategyRunner
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-hitl", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_SAFE_PLAN = '{"steps": [{"id": "s1", "summary": "collect facts"}]}'
_RISKY_PLAN = '{"steps": [{"id": "s1", "summary": "delete the production database"}]}'
_RESPONSES = {
    "supervisor": ["done", "final"],
    "goal_tree": ["done", "final"],
    "debate": ["proposal", "critique", "proposer-a"],
}


class _Gateway:
    def __init__(self, status: ApprovalStatus) -> None:
        self.status = status
        self.requests: list[dict[str, Any]] = []

    async def request_approval_async(self, **kwargs: Any) -> str:
        self.requests.append(kwargs)
        return f"req-{len(self.requests)}"

    async def wait_for_approval(self, request_id: str, **_: Any) -> ApprovalStatus:
        return self.status


def _request(strategy_id: str) -> StrategyExecutionRequest:
    return StrategyExecutionRequest.model_validate(
        {
            "tenant_id": CTX.tenant_id,
            "goal_id": f"goal-{strategy_id}",
            "strategy_id": strategy_id,
            "adapter_version": "1.0.0",
            "state_schema_version": 1,
            "agent_id": "agent-1",
            "runtime_profile_ref": "profile-1",
            "context_snapshot_ref": f"ctx-{strategy_id}",
            "policy_ref": "policy-1",
            "budget_ref": "budget-1",
            "cancellation_token": f"goal-{strategy_id}",
            "deadline": datetime.now(UTC) + timedelta(minutes=1),
            "idempotency_key": f"idem-{strategy_id}",
        }
    )


def _limits() -> PatternLimits:
    return PatternLimits.model_validate(
        {
            "calls": 32,
            "nodes": 32,
            "edges": 32,
            "depth": 8,
            "fan_out": 8,
            "rounds": 16,
            "tokens": 20_000,
            "duration_seconds": 30,
            "cost_usd": 5.0,
        }
    )


async def _run(
    strategy_id: str, goal: str, responses: list[str], gateway: Any
) -> tuple[Any, list[dict[str, Any]], FakeProvider]:
    events: list[dict[str, Any]] = []

    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    provider = FakeProvider(responses=responses)
    store = StrategyGoalContextStore()
    await store.put(
        f"ctx-{strategy_id}",
        StrategyGoalContext(goal_text=goal, provider=provider, tenant_ctx=CTX, event_callback=sink),
    )
    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(context_store=store, hitl_gateway=gateway),
        admission=default_distributed_admission,
    )
    return await runner.run(_request(strategy_id), _limits()), events, provider


def _responses(strategy_id: str, plan: str = _SAFE_PLAN) -> list[str]:
    head = [plan] if strategy_id != "debate" else []
    return head + _RESPONSES[strategy_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_id", ["supervisor", "goal_tree", "debate"])
async def test_safe_goal_runs_without_an_approval(strategy_id: str) -> None:
    gateway = _Gateway(ApprovalStatus.APPROVED)
    result, _, _ = await _run(strategy_id, "Summarise the report", _responses(strategy_id), gateway)
    assert result.terminal_state is ExecutionTerminalState.SUCCEEDED, result
    assert gateway.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_id", ["supervisor", "goal_tree", "debate"])
async def test_high_risk_goal_without_a_gateway_fails_closed(strategy_id: str) -> None:
    result, _, provider = await _run(
        strategy_id, "Delete the production database", _responses(strategy_id), None
    )
    assert result.terminal_state is ExecutionTerminalState.FAILED
    assert "approval_unavailable" in result.trace_summary.reason_codes
    assert provider.call_history == []  # nothing ran


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_id", ["supervisor", "goal_tree", "debate"])
@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (ApprovalStatus.REJECTED, "approval_rejected"),
        (ApprovalStatus.TIMED_OUT, "approval_timed_out"),
    ],
)
async def test_rejected_or_timed_out_approval_fails_the_goal_with_the_reason(
    strategy_id: str, status: ApprovalStatus, reason: str
) -> None:
    gateway = _Gateway(status)
    result, events, provider = await _run(
        strategy_id, "Deploy the release to prod", _responses(strategy_id), gateway
    )
    assert result.terminal_state is ExecutionTerminalState.FAILED
    assert reason in result.trace_summary.reason_codes
    assert gateway.requests and gateway.requests[0]["require_persisted"] is True
    assert provider.call_history == []
    types = [e["type"] for e in events]
    assert types == ["waiting_approval", "approval_denied"]


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_id", ["supervisor", "goal_tree", "debate"])
async def test_approved_high_risk_goal_resumes_and_completes(strategy_id: str) -> None:
    gateway = _Gateway(ApprovalStatus.APPROVED)
    result, events, _ = await _run(
        strategy_id, "Deploy the release to prod", _responses(strategy_id), gateway
    )
    assert result.terminal_state is ExecutionTerminalState.SUCCEEDED, result
    assert len(gateway.requests) == 1
    assert [e["type"] for e in events] == ["waiting_approval", "approval_granted"]


@pytest.mark.asyncio
@pytest.mark.parametrize("strategy_id", ["supervisor", "goal_tree"])
async def test_high_risk_sub_task_needs_approval_before_any_child_runs(strategy_id: str) -> None:
    rejected = _Gateway(ApprovalStatus.REJECTED)
    result, _, provider = await _run(
        strategy_id, "Tidy the reporting data", _responses(strategy_id, _RISKY_PLAN), rejected
    )
    assert result.terminal_state is ExecutionTerminalState.FAILED
    assert "approval_rejected" in result.trace_summary.reason_codes
    assert len(provider.call_history) == 1  # only the decomposition ran
    assert "sub-tasks" in rejected.requests[0]["action"]

    approved = _Gateway(ApprovalStatus.APPROVED)
    ok, _, _ = await _run(
        strategy_id, "Tidy the reporting data", _responses(strategy_id, _RISKY_PLAN), approved
    )
    assert ok.terminal_state is ExecutionTerminalState.SUCCEEDED, ok
    assert len(approved.requests) == 1


@pytest.mark.asyncio
async def test_sub_task_restating_the_approved_goal_is_not_asked_again() -> None:
    gateway = _Gateway(ApprovalStatus.APPROVED)
    # An unparseable plan falls back to the goal itself as the single sub-task.
    result, _, _ = await _run(
        "supervisor", "Deploy the release to prod", ["not json", "done", "final"], gateway
    )
    assert result.terminal_state is ExecutionTerminalState.SUCCEEDED, result
    assert len(gateway.requests) == 1


def test_worker_loop_runs_every_runner_strategy_with_the_gates() -> None:
    """The worker used to build a loop only for coordination patterns, so a
    queued supervisor/debate goal fell back to the local kernel (no gate)."""
    from types import SimpleNamespace

    from app.coordination.pattern_runs.goal_bridge import build_worker_distributed_loop
    from app.orchestration.runtime_profile import StrategySelection

    gateway = _Gateway(ApprovalStatus.APPROVED)
    for strategy_id in ("supervisor", "goal_tree", "debate", "voyager", "camel"):
        profile = SimpleNamespace(primary_strategy=StrategySelection(strategy_id, "1.0.0"))
        loop = build_worker_distributed_loop(
            profile, db_factory=object(), provider=object(), hitl_gateway=gateway
        )
        assert loop is not None, strategy_id
        executor = loop.strategy_runner._executor
        assert executor._hitl_gateway is gateway
        assert executor._skill_store is not None
    react = SimpleNamespace(primary_strategy=StrategySelection("react", "1.0.0"))
    assert build_worker_distributed_loop(react, db_factory=object(), provider=object()) is None
