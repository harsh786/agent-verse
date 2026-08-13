from __future__ import annotations

import asyncio

import pytest

from app.agent.patterns.autogpt import AutoGPTRuntime
from app.agent.patterns.babyagi import BabyAGIRuntime
from app.coordination.patterns.common import InMemoryPatternCheckpointStore


@pytest.mark.asyncio
async def test_babyagi_deduplicates_prioritizes_and_resumes_without_repeat() -> None:
    calls: list[str] = []
    runtime = BabyAGIRuntime(checkpoint_store=InMemoryPatternCheckpointStore())

    async def execute(task):
        calls.append(task.safe_summary)
        return {
            "result_reference": f"artifact://{task.work_item_id}",
            "objective_complete": task.work_item_id == "b",
        }

    kwargs = {
        "session_id": "session",
        "execution_id": "execution",
        "objective": "finish",
        "create_tasks": lambda _: [
            {"work_item_id": "b", "safe_summary": "second", "priority": 2},
            {"work_item_id": "a", "safe_summary": "first", "priority": 1},
            {"work_item_id": "a", "safe_summary": "duplicate", "priority": 3},
        ],
        "execute_task": execute,
        "maximum_tasks": 3,
    }
    result = await runtime.execute(**kwargs)
    resumed = await runtime.execute(**kwargs)
    assert result == resumed and result.phase == "completed"
    assert calls == ["first", "second"]


@pytest.mark.asyncio
async def test_autogpt_gates_actions_stops_stagnation_and_cancels() -> None:
    runtime = AutoGPTRuntime(checkpoint_store=InMemoryPatternCheckpointStore())
    denied = await runtime.execute(
        session_id="denied",
        execution_id="denied",
        objective="act",
        plan_action=lambda *_: {"action_id": "shell", "safe_summary": "run"},
        pre_execution_gate=lambda *_: False,
        execute_action=lambda *_: pytest.fail("denied"),
        maximum_actions=2,
        maximum_stagnation=1,
    )
    assert denied.phase == "failed" and denied.terminal_reason == "pre_execution_denied"
    cancelled = asyncio.Event()
    cancelled.set()
    stopped = await runtime.execute(
        session_id="cancel",
        execution_id="cancel",
        objective="act",
        plan_action=lambda *_: pytest.fail("cancelled"),
        pre_execution_gate=lambda *_: True,
        execute_action=lambda *_: pytest.fail("cancelled"),
        maximum_actions=2,
        maximum_stagnation=1,
        cancelled=cancelled,
    )
    assert stopped.phase == "cancelled"
