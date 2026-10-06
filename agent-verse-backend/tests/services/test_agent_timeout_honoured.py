"""B7 open item 5: the agent's ``timeout_seconds`` was stored and never read.

``agents.timeout_seconds`` (default 300) is accepted by POST/PATCH /agents and
returned by GET, but neither the in-process GoalService runner nor the Celery
``run_goal`` worker looked at it: both capped a goal at the plan's
``goal_timeout_seconds`` only (30 min - 24 h). The effective budget is now
min(plan goal timeout, agent timeout).
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import GoalService
from app.tenancy.context import PLAN_LIMITS, PlanTier, TenantContext
from app.tenancy.limits import effective_goal_timeout

_CTX = TenantContext(tenant_id="agt-timeout-t", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


# ── The rule ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("plan_s", "agent_s", "expected"),
    [
        (1800, 300, (300, "agent")),  # the agent's shorter budget binds
        (60, 300, (60, "plan")),  # the plan is the ceiling, never extended
        (1800, 1800, (1800, "plan")),
        (1800, None, (1800, "plan")),  # no agent / no value
        (1800, 0, (1800, "plan")),  # non-positive = no agent limit
        (1800, -5, (1800, "plan")),
        (1800, True, (1800, "plan")),  # a bool is not a number of seconds
        (1800, "60", (1800, "plan")),  # nor a string
        (1800, float("nan"), (1800, "plan")),
        (1800, 0.05, (0.05, "agent")),
    ],
)
def test_effective_goal_timeout_is_min_of_plan_and_agent(
    plan_s: float, agent_s: Any, expected: tuple[float, str]
) -> None:
    assert effective_goal_timeout(plan_s, agent_s) == expected


def test_effective_goal_timeout_ignores_test_doubles() -> None:
    """A MagicMock converts to 1.0 with float(): it must not become a 1 s budget."""
    assert effective_goal_timeout(1800, MagicMock()) == (1800, "plan")


def test_effective_goal_timeout_renders_whole_seconds_as_int() -> None:
    seconds, _ = effective_goal_timeout(1800.0, 300)
    assert f"{seconds}s" == "300s"


# ── In-process runner (GoalService) ───────────────────────────────────────────


def test_api_loop_carries_the_agent_timeout() -> None:
    class _Agents:
        def get(self, agent_id: str, *, tenant_ctx: TenantContext) -> dict[str, Any] | None:
            if agent_id == "agent-t":
                return {"agent_id": "agent-t", "timeout_seconds": 42}
            return None

    app_state = MagicMock()
    for name in (
        "audit_log", "cost_controller", "redis_cost_controller", "hitl_gateway",
        "knowledge_store", "long_term_memory", "eval_runner", "policy_engine",
        "permission_matrix",
    ):
        setattr(app_state, name, None)
    app_state._llm_configs = {}
    app_state.agent_store = _Agents()
    svc = GoalService()
    svc._app_state = app_state

    loop = svc._make_agent_loop_for_tenant(_CTX, app_state, agent_id="agent-t")
    assert loop._agent_timeout_seconds == 42

    no_agent = svc._make_agent_loop_for_tenant(_CTX, app_state)
    assert no_agent._agent_timeout_seconds is None


class _HangingLoop:
    def __init__(self, agent_timeout: Any) -> None:
        self._agent_timeout_seconds = agent_timeout

    async def run(self, **kwargs: Any) -> None:
        await asyncio.sleep(10)  # a stuck tool call


async def _run_hanging(agent_timeout: Any) -> Any:
    class _Svc(GoalService):
        def _make_agent_loop_for_tenant(self, *args: Any, **kwargs: Any) -> _HangingLoop:
            return _HangingLoop(agent_timeout)

    svc = _Svc()  # no task_queue: the in-process path
    result = await svc.submit_goal(
        goal="do something that hangs", priority="normal", dry_run=False, tenant_ctx=_CTX
    )
    record = svc._goals[result["goal_id"]]
    assert record.task is not None
    await asyncio.wait_for(record.task, timeout=5.0)
    return record


async def test_in_process_goal_is_stopped_at_the_agent_timeout() -> None:
    """Plan budget 2 h (PROFESSIONAL), agent budget 0.05 s: the agent's binds."""
    assert PLAN_LIMITS[_CTX.plan].goal_timeout_seconds > 60
    record = await _run_hanging(0.05)

    assert record.status == GoalStatus.FAILED
    assert record.error_message == "Goal timed out after 0.05s (agent timeout_seconds)"


async def test_in_process_plan_timeout_still_binds_below_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        PLAN_LIMITS,
        _CTX.plan,
        dataclasses.replace(PLAN_LIMITS[_CTX.plan], goal_timeout_seconds=0.05),
    )
    record = await _run_hanging(300)

    assert record.status == GoalStatus.FAILED
    assert record.error_message == "Goal timed out after 0.05s"
