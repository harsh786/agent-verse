"""Tests for SupervisorAgent multi-agent pattern."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from app.agent.supervisor import SupervisorAgent, SubAgentTask, SupervisionResult


def test_sub_agent_task_defaults() -> None:
    t = SubAgentTask(goal="do something")
    assert t.status == "pending"
    assert t.task_id is not None


@pytest.mark.asyncio
async def test_supervisor_decompose_fallback() -> None:
    """When LLM decompose fails, falls back to single task."""
    from app.providers.fake import FakeProvider

    fake = FakeProvider(responses=["invalid json"])
    supervisor = SupervisorAgent(
        planner_provider=fake, goal_service=None, max_parallel=2
    )
    tasks = await supervisor._decompose("do the thing", None)
    assert len(tasks) >= 1
    assert tasks[0].goal == "do the thing"


def test_supervision_result() -> None:
    r = SupervisionResult(success=True, tasks=[], synthesized_result="done")
    assert r.success is True


# ---------------------------------------------------------------------------
# New tests for Fix 1 (_decompose model) and Fix 2 (_synthesize LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decompose_uses_non_empty_model() -> None:
    """_decompose must pass a non-empty model string to the provider."""
    captured_requests: list[Any] = []

    class CapturingProvider:
        _default_model = "test-model-v1"

        async def complete(self, request: Any) -> Any:
            captured_requests.append(request)
            resp = MagicMock()
            resp.content = '{"sub_tasks": [{"goal": "sub1", "optional": false}]}'
            return resp

    provider = CapturingProvider()
    supervisor = SupervisorAgent(
        planner_provider=provider, goal_service=None, max_parallel=1
    )
    tasks = await supervisor._decompose("complex goal", tenant_ctx=None)

    assert captured_requests, "provider.complete was not called"
    req = captured_requests[0]
    assert req.model, "model must be a non-empty string"
    assert req.model == "test-model-v1", (
        f"Expected _default_model 'test-model-v1', got '{req.model}'"
    )
    assert tasks[0].goal == "sub1"


@pytest.mark.asyncio
async def test_decompose_uses_default_model_fallback_when_attribute_absent() -> None:
    """If provider has no _default_model, _decompose falls back to a non-empty string."""
    captured: list[Any] = []

    class NoModelProvider:
        # no _default_model attribute
        async def complete(self, request: Any) -> Any:
            captured.append(request)
            resp = MagicMock()
            resp.content = '{"sub_tasks": [{"goal": "task A"}]}'
            return resp

    supervisor = SupervisorAgent(
        planner_provider=NoModelProvider(), goal_service=None, max_parallel=1
    )
    await supervisor._decompose("do something", None)

    if captured:
        # The model should be non-empty (falls back to hardcoded default)
        assert captured[0].model != "", "model must not be empty string"


@pytest.mark.asyncio
async def test_synthesize_calls_provider_complete() -> None:
    """_synthesize should invoke provider.complete to produce the synthesis."""
    complete_calls: list[Any] = []

    class CapturingProvider:
        _default_model = "synthesis-model"

        async def complete(self, request: Any) -> Any:
            complete_calls.append(request)
            resp = MagicMock()
            resp.content = "Synthesized answer from LLM"
            return resp

    supervisor = SupervisorAgent(
        planner_provider=CapturingProvider(), goal_service=None
    )
    completed = [
        SubAgentTask(goal="sub A", status="complete", result="result of A"),
        SubAgentTask(goal="sub B", status="complete", result="result of B"),
    ]
    failed: list[SubAgentTask] = []

    synthesis = await supervisor._synthesize(
        "original complex goal", completed, failed, tenant_ctx=None
    )

    assert complete_calls, "provider.complete was never called during synthesis"
    assert synthesis == "Synthesized answer from LLM"


@pytest.mark.asyncio
async def test_synthesize_skips_llm_when_all_failed() -> None:
    """When all tasks failed, _synthesize returns without calling the LLM."""
    call_count = 0

    class CountingProvider:
        _default_model = "m"

        async def complete(self, request: Any) -> Any:
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.content = "should not appear"
            return resp

    supervisor = SupervisorAgent(
        planner_provider=CountingProvider(), goal_service=None
    )
    result = await supervisor._synthesize("goal", [], [SubAgentTask(goal="x")], None)

    assert call_count == 0, "LLM should not be called when there are no completed tasks"
    assert "failed" in result.lower()


@pytest.mark.asyncio
async def test_synthesize_falls_back_to_text_when_llm_raises() -> None:
    """When the LLM call raises, _synthesize falls back to structured text."""

    class ErrorProvider:
        _default_model = "m"

        async def complete(self, request: Any) -> Any:
            raise RuntimeError("LLM unavailable")

    supervisor = SupervisorAgent(
        planner_provider=ErrorProvider(), goal_service=None
    )
    completed = [SubAgentTask(goal="task1", status="complete", result="res1")]
    result = await supervisor._synthesize("goal", completed, [], None)

    # Fallback path produces structured text (not the LLM response)
    assert result, "Fallback must return a non-empty string"
    assert "task1" in result or "1/" in result or "completed" in result.lower()
