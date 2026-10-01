"""GOAL-STALL (RW-21): a step can never hang a goal silently.

A step awaited its provider / tool calls with no deadline and no progress event,
so a hung completion left the goal "executing" with no events for minutes. The
step watchdog emits ``step_heartbeat`` events while a step runs and cancels it
after its active-time deadline (approval waits excluded), recording a FAILED
step so the verify / replan path takes over.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agent.graph import AgentGraph
from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
from app.agent.step_watchdog import (
    StepDeadlineExceededError,
    approval_wait,
    run_step_with_deadline,
)
from app.governance.hitl import ApprovalStatus, HITLGateway
from app.providers.base import CompletionResponse
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

T = TenantContext(tenant_id="watchdog-t1", plan=PlanTier.ENTERPRISE, api_key_id="w")


class _HungProvider(FakeProvider):
    """Never answers (a provider stuck mid-completion)."""

    def __init__(self) -> None:
        super().__init__(responses=["never"])
        self.started = 0

    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        self.started += 1
        await asyncio.Event().wait()

    async def stream_tokens(self, request: Any, on_token: Any) -> Any:  # type: ignore[override]
        self.started += 1
        await asyncio.Event().wait()

    async def stream_complete(self, request: Any) -> Any:  # type: ignore[override]
        self.started += 1
        await asyncio.Event().wait()
        yield ""  # pragma: no cover - never reached


class _Planner(FakeProvider):
    async def complete(self, request: Any) -> Any:  # type: ignore[override]
        schema = getattr(request, "response_schema", None) or {}
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        content = (
            '{"steps": ["Summarize the weather report"]}'
            if "steps" in props
            else '{"success": false, "reason": "step failed"}'
        )
        return CompletionResponse(content=content, model="fake", input_tokens=1, output_tokens=1)


def _collect(graph: AgentGraph) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def _cb(event: dict[str, Any]) -> None:
        events.append(event)

    graph._event_callback = _cb  # type: ignore[assignment]
    return events


async def test_hung_step_is_cancelled_at_its_deadline_with_heartbeats() -> None:
    executor = _HungProvider()
    graph = AgentGraph(planner=_Planner(), executor=executor, verifier=_Planner())
    graph._step_timeout_s = 0.6  # type: ignore[attr-defined]
    graph._step_heartbeat_s = 0.1  # type: ignore[attr-defined]
    events = _collect(graph)
    state = AgentState(goal="Summarize the weather report", tenant_ctx=T)
    state.steps.append(StepResult(description="Summarize", status=StepStatus.RUNNING))

    started = time.monotonic()
    with pytest.raises(StepDeadlineExceededError, match="deadline"):
        await graph._execute_step("Summarize the weather report", state, T)
    assert time.monotonic() - started < 5.0
    assert executor.started >= 1
    beats = [e for e in events if e.get("type") == "step_heartbeat"]
    assert len(beats) >= 3 and beats[-1]["elapsed_s"] > 0
    assert [e for e in events if e.get("type") == "step_timeout"]


async def test_full_goal_with_a_hung_provider_ends_instead_of_stalling() -> None:
    graph = AgentGraph(
        planner=_Planner(), executor=_HungProvider(), verifier=_Planner(), max_iterations=2
    )
    graph._step_timeout_s = 0.5  # type: ignore[attr-defined]
    graph._step_heartbeat_s = 0.1  # type: ignore[attr-defined]
    events: list[dict[str, Any]] = []

    async def _cb(event: dict[str, Any]) -> None:
        events.append(event)

    state = await asyncio.wait_for(
        graph.run(goal="Summarize the weather", tenant_ctx=T, event_callback=_cb), 30
    )

    assert state.status != GoalStatus.COMPLETE
    failed = [e for e in events if e.get("type") == "step_failed"]
    assert failed and "deadline" in str(failed[0].get("error")), failed
    assert any(e.get("type") == "step_heartbeat" for e in events)


async def test_waiting_for_an_approval_does_not_count_against_the_deadline() -> None:
    executor = FakeProvider(responses=["deployed the service"])
    hitl = HITLGateway()

    async def _slow_decision(*args: Any, **kwargs: Any) -> ApprovalStatus:
        await asyncio.sleep(0.8)  # the human takes longer than the step deadline
        return ApprovalStatus.APPROVED

    hitl.wait_for_approval = AsyncMock(side_effect=_slow_decision)  # type: ignore[method-assign]
    graph = AgentGraph(
        planner=_Planner(),
        executor=executor,
        verifier=_Planner(),
        hitl_gateway=hitl,
        autonomy_mode="supervised",
    )
    graph._step_timeout_s = 0.4  # type: ignore[attr-defined]
    graph._step_heartbeat_s = 0.1  # type: ignore[attr-defined]
    events = _collect(graph)
    step = "deploy the service to production"
    state = AgentState(goal=step, tenant_ctx=T)
    state.steps.append(StepResult(description=step, status=StepStatus.RUNNING))

    output = await graph._execute_step(step, state, T)

    assert "deployed" in output
    beats = [e for e in events if e.get("type") == "step_heartbeat"]
    assert any(b.get("waiting_for_approval") for b in beats)
    assert not [e for e in events if e.get("type") == "step_timeout"]


async def test_watchdog_returns_result_and_propagates_errors() -> None:
    async def _ok() -> str:
        return "done"

    async def _boom() -> str:
        raise PermissionError("denied")

    async def _emit(_: dict[str, Any]) -> None:
        return None

    assert await run_step_with_deadline("s", _ok, emit=_emit, timeout_s=1, heartbeat_s=1) == "done"
    with pytest.raises(PermissionError, match="denied"):
        await run_step_with_deadline("s", _boom, emit=_emit, timeout_s=1, heartbeat_s=1)


async def test_outer_cancellation_takes_the_step_down() -> None:
    cancelled = asyncio.Event()

    async def _hang() -> str:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return ""

    async def _emit(_: dict[str, Any]) -> None:
        return None

    task = asyncio.ensure_future(
        run_step_with_deadline("s", _hang, emit=_emit, timeout_s=60, heartbeat_s=0.05)
    )
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


def test_approval_wait_outside_a_step_is_a_no_op() -> None:
    with approval_wait():
        pass
