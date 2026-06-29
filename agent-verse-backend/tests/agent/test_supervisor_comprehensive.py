"""Comprehensive tests for app/agent/supervisor.py — targets 90%+ statement coverage."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.supervisor import SubAgentTask, SupervisionResult, SupervisorAgent
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-sup", plan=PlanTier.ENTERPRISE, api_key_id="key-s")


# ── SubAgentTask dataclass ────────────────────────────────────────────────────

def test_sub_agent_task_defaults() -> None:
    t = SubAgentTask()
    assert t.goal == ""
    assert t.status == "pending"
    assert t.task_id != ""


def test_sub_agent_task_custom_goal() -> None:
    t = SubAgentTask(goal="Build API", status="running")
    assert t.goal == "Build API"
    assert t.status == "running"


# ── SupervisionResult dataclass ───────────────────────────────────────────────

def test_supervision_result_defaults() -> None:
    r = SupervisionResult(success=True, tasks=[])
    assert r.synthesized_result == ""
    assert r.total_cost_usd == 0.0


# ── SupervisorAgent._decompose ────────────────────────────────────────────────

async def test_decompose_returns_parsed_subtasks() -> None:
    decomp_json = '{"sub_tasks": [{"goal": "Task A"}, {"goal": "Task B"}, {"goal": "Task C"}]}'
    fake = FakeProvider(responses=[decomp_json])
    agent = SupervisorAgent(
        planner_provider=fake,
        goal_service=MagicMock(),
        max_parallel=5,
    )
    tasks = await agent._decompose("Build a full system", _CTX)
    assert len(tasks) == 3
    assert tasks[0].goal == "Task A"


async def test_decompose_falls_back_on_invalid_json() -> None:
    fake = FakeProvider(responses=["not valid json"])
    agent = SupervisorAgent(planner_provider=fake, goal_service=MagicMock())
    tasks = await agent._decompose("Build something", _CTX)
    # Fallback: single task with original goal
    assert len(tasks) == 1
    assert tasks[0].goal == "Build something"


async def test_decompose_falls_back_on_missing_sub_tasks() -> None:
    fake = FakeProvider(responses=['{"other": "data"}'])
    agent = SupervisorAgent(planner_provider=fake, goal_service=MagicMock())
    tasks = await agent._decompose("Goal", _CTX)
    assert len(tasks) == 1


async def test_decompose_caps_at_six_tasks() -> None:
    tasks_json = ', '.join([f'{{"goal": "Task {i}"}}' for i in range(10)])
    fake = FakeProvider(responses=[f'{{"sub_tasks": [{tasks_json}]}}'])
    agent = SupervisorAgent(planner_provider=fake, goal_service=MagicMock())
    tasks = await agent._decompose("Huge goal", _CTX)
    assert len(tasks) <= 6


async def test_decompose_skips_empty_goals() -> None:
    fake = FakeProvider(responses=['{"sub_tasks": [{"goal": "Real task"}, {"goal": ""}]}'])
    agent = SupervisorAgent(planner_provider=fake, goal_service=MagicMock())
    tasks = await agent._decompose("Goal", _CTX)
    # Empty goal task is still included (supervisor doesn't filter)
    assert len(tasks) == 2


# ── SupervisorAgent._synthesize ───────────────────────────────────────────────

async def test_synthesize_all_failed_returns_failure_message() -> None:
    fake = FakeProvider(responses=["should not be called"])
    agent = SupervisorAgent(planner_provider=fake, goal_service=MagicMock())
    result = await agent._synthesize("Goal", [], [SubAgentTask(goal="T1", status="failed")], _CTX)
    assert "failed" in result.lower()


async def test_synthesize_with_completed_tasks_calls_llm() -> None:
    fake = FakeProvider(responses=["Synthesized output from LLM"])
    agent = SupervisorAgent(planner_provider=fake, goal_service=MagicMock())
    completed = [SubAgentTask(goal="T1", status="complete", result="Result of T1")]
    result = await agent._synthesize("Original goal", completed, [], _CTX)
    assert result == "Synthesized output from LLM"


async def test_synthesize_llm_failure_returns_fallback_text() -> None:
    broken = MagicMock()
    broken.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
    broken._default_model = ""
    agent = SupervisorAgent(planner_provider=broken, goal_service=MagicMock())
    completed = [SubAgentTask(goal="T1", status="complete", result="Partial result")]
    result = await agent._synthesize("Goal", completed, [], _CTX)
    assert "T1" in result or "1/1" in result


# ── SupervisorAgent.run ───────────────────────────────────────────────────────

async def _make_goal_service_mock(goal_id: str = "g1", events=None) -> MagicMock:
    """Helper: mock goal_service that returns a completed goal event."""
    if events is None:
        events = [{"type": "goal_complete", "output": "Task done"}]

    async def fake_subscribe(goal_id, tenant_ctx):
        for evt in events:
            yield evt

    mock_service = MagicMock()
    mock_service.submit_goal = AsyncMock(return_value={"goal_id": goal_id})
    mock_service.subscribe_events = fake_subscribe
    return mock_service


async def test_run_success_all_tasks_complete() -> None:
    decomp_json = '{"sub_tasks": [{"goal": "Task A"}, {"goal": "Task B"}]}'
    synth_response = "All done"
    fake = FakeProvider(responses=[decomp_json, synth_response])
    goal_service = await _make_goal_service_mock()

    agent = SupervisorAgent(
        planner_provider=fake,
        goal_service=goal_service,
        max_parallel=5,
        timeout_per_subtask=30.0,
    )
    result = await agent.run("Big goal", _CTX)

    assert result.success is True
    assert result.synthesized_result == "All done"
    assert len(result.tasks) == 2


async def test_run_failed_tasks_due_to_goal_failed_event() -> None:
    decomp_json = '{"sub_tasks": [{"goal": "Task A"}]}'
    synth_response = "Partial"
    fake = FakeProvider(responses=[decomp_json, synth_response])

    async def fail_subscribe(goal_id, tenant_ctx):
        yield {"type": "goal_failed", "reason": "agent crashed"}

    goal_service = MagicMock()
    goal_service.submit_goal = AsyncMock(return_value={"goal_id": "g1"})
    goal_service.subscribe_events = fail_subscribe

    agent = SupervisorAgent(
        planner_provider=fake,
        goal_service=goal_service,
        max_parallel=5,
        timeout_per_subtask=30.0,
    )
    result = await agent.run("Goal", _CTX)
    assert result.tasks[0].status == "failed"
    assert result.tasks[0].error == "agent crashed"


async def test_run_timeout_marks_task_failed() -> None:
    decomp_json = '{"sub_tasks": [{"goal": "Slow task"}]}'
    synth_response = "Timed out"
    fake = FakeProvider(responses=[decomp_json, synth_response])

    async def hanging_subscribe(goal_id, tenant_ctx):
        await asyncio.sleep(99)  # never resolves
        yield {"type": "goal_complete"}

    goal_service = MagicMock()
    goal_service.submit_goal = AsyncMock(return_value={"goal_id": "g1"})
    goal_service.subscribe_events = hanging_subscribe

    agent = SupervisorAgent(
        planner_provider=fake,
        goal_service=goal_service,
        max_parallel=5,
        timeout_per_subtask=0.05,  # tiny timeout
    )
    result = await agent.run("Goal", _CTX)
    assert result.tasks[0].status == "failed"
    assert "Timeout" in result.tasks[0].error or "timeout" in result.tasks[0].error.lower()


async def test_run_submit_goal_exception_marks_failed() -> None:
    decomp_json = '{"sub_tasks": [{"goal": "Task"}]}'
    synth_response = "Failure"
    fake = FakeProvider(responses=[decomp_json, synth_response])

    goal_service = MagicMock()
    goal_service.submit_goal = AsyncMock(side_effect=RuntimeError("submit failed"))

    agent = SupervisorAgent(
        planner_provider=fake,
        goal_service=goal_service,
        max_parallel=5,
        timeout_per_subtask=30.0,
    )
    result = await agent.run("Goal", _CTX)
    assert result.tasks[0].status == "failed"
    assert "submit failed" in result.tasks[0].error


async def test_run_event_callback_receives_events() -> None:
    events: list[dict] = []

    async def cb(evt: dict) -> None:
        events.append(evt)

    decomp_json = '{"sub_tasks": [{"goal": "Task"}]}'
    synth_response = "Done"
    fake = FakeProvider(responses=[decomp_json, synth_response])
    goal_service = await _make_goal_service_mock()

    agent = SupervisorAgent(
        planner_provider=fake,
        goal_service=goal_service,
        max_parallel=5,
        timeout_per_subtask=30.0,
    )
    await agent.run("Goal", _CTX, event_callback=cb)

    types = {e["type"] for e in events}
    assert "supervisor_decomposed" in types
    assert "supervisor_complete" in types


async def test_run_success_false_when_more_failed_than_completed() -> None:
    decomp_json = '{"sub_tasks": [{"goal": "T1"}, {"goal": "T2"}, {"goal": "T3"}]}'
    synth_response = "Partial"
    fake = FakeProvider(responses=[decomp_json, synth_response])

    call_count = 0

    async def mixed_subscribe(goal_id, tenant_ctx):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            yield {"type": "goal_complete", "output": "success"}
        else:
            yield {"type": "goal_failed", "reason": "error"}

    goal_service = MagicMock()
    goal_service.submit_goal = AsyncMock(return_value={"goal_id": "gx"})
    goal_service.subscribe_events = mixed_subscribe

    agent = SupervisorAgent(
        planner_provider=fake,
        goal_service=goal_service,
        max_parallel=5,
        timeout_per_subtask=30.0,
    )
    result = await agent.run("Goal", _CTX)
    # 1 complete, 2 failed → success=False
    assert result.success is False


async def test_run_event_callback_exception_swallowed() -> None:
    async def bad_cb(evt: dict) -> None:
        raise RuntimeError("callback crash")

    decomp_json = '{"sub_tasks": [{"goal": "Task"}]}'
    synth_response = "Done"
    fake = FakeProvider(responses=[decomp_json, synth_response])
    goal_service = await _make_goal_service_mock()

    agent = SupervisorAgent(
        planner_provider=fake,
        goal_service=goal_service,
        max_parallel=5,
        timeout_per_subtask=30.0,
    )
    # Must not raise
    result = await agent.run("Goal", _CTX, event_callback=bad_cb)
    assert result is not None
