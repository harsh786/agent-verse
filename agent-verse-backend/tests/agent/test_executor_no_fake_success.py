"""Regression tests: the executor must never report a step as done without a real result.

Covers (audit item 1):
  a. dedup returned the literal "Duplicate step, returning cached result." with no
     cache behind it;
  b. an open circuit breaker returned "Circuit open, step skipped." AS the step output
     (so the step was marked COMPLETE);
  c. a timed-out policy approval (REQUIRE_APPROVAL) proceeded to execute the step.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.graph import AgentGraph
from app.agent.graph_types import StepNotExecutedError
from app.agent.state import AgentState, StepResult, StepStatus
from app.governance.hitl import ApprovalStatus
from app.governance.policies import PolicyResult
from app.providers.fake import FakeProvider
from app.reliability.circuit_breaker import CircuitBreaker
from app.reliability.dedup import DeduplicationCache
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.asyncio

T = TenantContext(tenant_id="no-fake-success", plan=PlanTier.ENTERPRISE, api_key_id="k1")


def _graph(executor: FakeProvider | None = None, **kwargs: Any) -> AgentGraph:
    return AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["do thing"]}']),
        executor=executor or FakeProvider(responses=["real step output from the executor"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        **kwargs,
    )


def _state(goal: str = "goal") -> AgentState:
    state = AgentState(goal=goal, tenant_ctx=T)
    state.goal_id = "goal-1"
    state.steps.append(StepResult(description="do thing", status=StepStatus.RUNNING))
    return state


# ── (a) dedup ────────────────────────────────────────────────────────────────


async def test_dedup_hit_returns_the_real_cached_output() -> None:
    executor = FakeProvider(responses=["first real output of the step", "second output"])
    graph = _graph(executor=executor, dedup_cache=DeduplicationCache())
    state = _state()

    first = await graph._execute_step("do thing", state, T)
    second = await graph._execute_step("do thing", state, T)

    assert first == "first real output of the step"
    # The duplicate is served the *actual* cached output — no placeholder string.
    assert second == first
    assert "Duplicate step" not in second
    assert len(executor.call_history) == 1


async def test_dedup_hit_without_cached_output_re_executes() -> None:
    """A hash marked seen but with no stored result must re-execute, not fake success."""
    dedup = MagicMock(spec=DeduplicationCache)
    dedup.is_duplicate.return_value = True
    dedup.get_result.return_value = None
    executor = FakeProvider(responses=["re-executed real output"])
    graph = _graph(executor=executor, dedup_cache=dedup)

    out = await graph._execute_step("do thing", _state(), T)

    assert out == "re-executed real output"
    assert len(executor.call_history) == 1


async def test_dedup_is_scoped_to_the_goal_run() -> None:
    executor = FakeProvider(responses=["output for goal one", "output for goal two"])
    graph = _graph(executor=executor, dedup_cache=DeduplicationCache())
    s1 = _state()
    s2 = _state()
    s2.goal_id = "goal-2"

    assert await graph._execute_step("do thing", s1, T) == "output for goal one"
    assert await graph._execute_step("do thing", s2, T) == "output for goal two"


# ── (b) circuit breaker ──────────────────────────────────────────────────────


async def test_open_circuit_raises_step_failed_not_output() -> None:
    breaker = CircuitBreaker()
    breaker.can_call = MagicMock(return_value=False)  # type: ignore[method-assign]
    executor = FakeProvider(responses=["must not run"])
    graph = _graph(executor=executor, circuit_breakers={"llm": breaker})

    with pytest.raises(StepNotExecutedError, match="Circuit breaker open"):
        await graph._execute_step("do thing", _state(), T)
    assert executor.call_history == []


async def test_open_circuit_marks_step_failed_and_goal_is_not_complete() -> None:
    breaker = CircuitBreaker()
    breaker.can_call = MagicMock(return_value=False)  # type: ignore[method-assign]
    graph = AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["do thing"]}']),
        executor=FakeProvider(responses=["must not run"]),
        # The LLM verifier would happily say "success" — the deterministic gate must win.
        verifier=FakeProvider(responses=['{"success": true, "reason": "looks fine"}']),
        circuit_breakers={"llm": breaker},
        max_iterations=2,
    )

    state = await graph.run(goal="do thing", tenant_ctx=T)

    assert state.steps, "the step must be recorded"
    assert all(s.status == StepStatus.FAILED for s in state.steps)
    assert all("ircuit" in (s.error or "") for s in state.steps)
    assert all(s.output != "Circuit open, step skipped." for s in state.steps)
    assert state.verification_success is False
    assert str(state.status) != "complete"


# ── (c) policy approval timeout ──────────────────────────────────────────────


@pytest.mark.parametrize("status", [ApprovalStatus.TIMED_OUT, ApprovalStatus.PENDING])
async def test_policy_approval_not_granted_denies_step(status: ApprovalStatus) -> None:
    policy = MagicMock()
    policy.evaluate.return_value = PolicyResult.REQUIRE_APPROVAL
    hitl = MagicMock()
    hitl.request_approval.return_value = "req-1"
    hitl.wait_for_approval = AsyncMock(return_value=status)
    executor = FakeProvider(responses=["must not run"])
    graph = _graph(
        executor=executor,
        policy_engine=policy,
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )

    with pytest.raises(PermissionError):
        await graph._execute_step("do thing", _state(), T)
    assert executor.call_history == []


# ── (d) safety checks fail closed ────────────────────────────────────────────


async def test_action_safety_profile_error_fails_closed() -> None:
    """An exception in the action-safety assessment must not let the step run."""
    from unittest.mock import patch

    executor = FakeProvider(responses=["should never be produced"])
    graph = _graph(executor=executor)
    with (
        patch(
            "app.security_runtime.action_safety_profile.ActionSafetyProfileSelector.select",
            side_effect=RuntimeError("selector broken"),
        ),
        pytest.raises(StepNotExecutedError, match="Action-safety assessment failed"),
    ):
        await graph._execute_step("do thing", _state(), T)
    assert executor.call_history == []


async def test_profile_guardrail_error_fails_closed() -> None:
    """An enabled profile GuardrailEnforcer that errors must block the step."""
    from types import SimpleNamespace
    from unittest.mock import patch

    executor = FakeProvider(responses=["should never be produced"])
    graph = _graph(executor=executor)
    state = _state()
    state.context["_runtime_profile"] = object()
    flags = SimpleNamespace(dynamic_orchestration=True, enable_guardrail_profile=True)
    with (
        patch("app.core.runtime_flags.get_runtime_flags", return_value=flags),
        patch(
            "app.security_runtime.guardrail_enforcer.GuardrailEnforcer.check_tool_args",
            AsyncMock(side_effect=RuntimeError("enforcer broken")),
        ),
        pytest.raises(StepNotExecutedError, match="Profile guardrail check failed"),
    ):
        await graph._execute_step("do thing", state, T)
    assert executor.call_history == []


@pytest.mark.parametrize("mode", ["bounded-autonomous", "fully-autonomous"])
async def test_policy_require_approval_outside_supervised_mode_is_not_executed(
    mode: str,
) -> None:
    """The request used to be filed (orphaned) and the step ran anyway."""
    policy = MagicMock()
    policy.evaluate.return_value = PolicyResult.REQUIRE_APPROVAL
    hitl = MagicMock()
    hitl.request_approval.return_value = "req-9"
    hitl.wait_for_approval = AsyncMock()
    executor = FakeProvider(responses=["must not run"])
    graph = _graph(executor=executor, policy_engine=policy, hitl_gateway=hitl, autonomy_mode=mode)

    with pytest.raises(PermissionError, match=r"tenant policy.*supervised mode"):
        await graph._execute_step("do thing", _state(), T)
    assert executor.call_history == []
    hitl.wait_for_approval.assert_not_awaited()
    # Nothing waits for a decision here, so no (orphaned) request is filed.
    hitl.request_approval.assert_not_called()


async def test_policy_require_approval_without_gateway_is_not_executed() -> None:
    """No approval gateway used to skip the policy entirely."""
    policy = MagicMock()
    policy.evaluate.return_value = PolicyResult.REQUIRE_APPROVAL
    executor = FakeProvider(responses=["must not run"])
    graph = _graph(executor=executor, policy_engine=policy, autonomy_mode="supervised")

    with pytest.raises(PermissionError, match="no approval gateway"):
        await graph._execute_step("do thing", _state(), T)
    assert executor.call_history == []
