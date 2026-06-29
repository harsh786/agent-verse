"""Comprehensive tests for app/agent/goal_tree.py — targets 90%+ statement coverage."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent.goal_tree import (
    DecompositionResult,
    _synthesize_goal_tree_results,
    decompose_goal,
    execute_goal_tree,
    execute_sub_goal,
)
from app.agent.state import AgentState, GoalStatus, SubGoal
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="tid-gt", plan=PlanTier.PROFESSIONAL, api_key_id="key-1")


# ── DecompositionResult ───────────────────────────────────────────────────────

def test_decomposition_result_default() -> None:
    r = DecompositionResult(should_decompose=False)
    assert r.sub_goals == []


def test_decomposition_result_with_subgoals() -> None:
    sg = SubGoal(sub_goal_id="sg1", description="Do A", parent_goal_id="p1")
    r = DecompositionResult(should_decompose=True, sub_goals=[sg])
    assert len(r.sub_goals) == 1


# ── decompose_goal ────────────────────────────────────────────────────────────

async def test_decompose_goal_returns_false_on_no_decompose() -> None:
    fake = FakeProvider(responses=['{"decompose": false}'])
    with patch("app.agent.goal_tree.GOAL_TREE_SYSTEM", "system prompt", create=True):
        # Patch the lazy import
        with patch("app.agent.prompts.GOAL_TREE_SYSTEM", "system prompt", create=True):
            result = await decompose_goal("Simple goal", fake, _CTX, "parent-1")
    assert result.should_decompose is False


async def test_decompose_goal_valid_json_with_subgoals() -> None:
    response = '{"decompose": true, "sub_goals": [{"id": "sg1", "description": "Step A", "depends_on": []}, {"id": "sg2", "description": "Step B", "depends_on": ["sg1"]}]}'
    fake = FakeProvider(responses=[response])
    result = await decompose_goal("Complex goal", fake, _CTX, "parent-1")
    assert result.should_decompose is True
    assert len(result.sub_goals) == 2
    assert result.sub_goals[0].sub_goal_id == "sg1"
    assert result.sub_goals[1].depends_on == ["sg1"]


async def test_decompose_goal_invalid_json_returns_false() -> None:
    fake = FakeProvider(responses=["not valid json at all"])
    result = await decompose_goal("Some goal", fake, _CTX, "parent-1")
    assert result.should_decompose is False


async def test_decompose_goal_missing_decompose_key_returns_false() -> None:
    fake = FakeProvider(responses=['{"other_key": true}'])
    result = await decompose_goal("Goal", fake, _CTX, "parent-1")
    assert result.should_decompose is False


async def test_decompose_goal_empty_subgoals_returns_false() -> None:
    fake = FakeProvider(responses=['{"decompose": true, "sub_goals": []}'])
    result = await decompose_goal("Goal", fake, _CTX, "parent-1")
    assert result.should_decompose is False


async def test_decompose_goal_subgoal_missing_id_uses_index() -> None:
    response = '{"decompose": true, "sub_goals": [{"description": "Task A"}]}'
    fake = FakeProvider(responses=[response])
    result = await decompose_goal("Goal", fake, _CTX, "parent-1")
    assert result.should_decompose is True
    # ID defaults to f"sg-{i}" when missing
    assert result.sub_goals[0].sub_goal_id == "sg-0"


async def test_decompose_goal_strips_markdown_fences() -> None:
    """LLM sometimes wraps JSON in ```json ... ``` fences."""
    response = '```json\n{"decompose": true, "sub_goals": [{"id": "s1", "description": "Do thing"}]}\n```'
    fake = FakeProvider(responses=[response])
    result = await decompose_goal("Goal with fence", fake, _CTX, "parent-1")
    assert result.should_decompose is True


# ── execute_sub_goal ──────────────────────────────────────────────────────────

async def test_execute_sub_goal_success() -> None:
    """Graph factory returns a completed state."""
    sg = SubGoal(sub_goal_id="sg1", description="Fetch data", parent_goal_id="p1")
    completed_state = MagicMock()
    completed_state.status = GoalStatus.COMPLETE
    completed_state.steps = [
        MagicMock(description="step1", output="result1"),
    ]

    graph = MagicMock()
    graph.run = AsyncMock(return_value=completed_state)
    graph_factory = MagicMock(return_value=graph)

    semaphore = asyncio.Semaphore(4)
    result = await execute_sub_goal(sg, tenant_ctx=_CTX, graph_factory=graph_factory, semaphore=semaphore)

    assert result.status == GoalStatus.COMPLETE
    assert "result1" in result.result


async def test_execute_sub_goal_exception_marks_failed() -> None:
    sg = SubGoal(sub_goal_id="sg2", description="Failing task", parent_goal_id="p1")

    graph = MagicMock()
    graph.run = AsyncMock(side_effect=RuntimeError("graph explosion"))
    graph_factory = MagicMock(return_value=graph)

    semaphore = asyncio.Semaphore(1)
    result = await execute_sub_goal(sg, tenant_ctx=_CTX, graph_factory=graph_factory, semaphore=semaphore)

    assert result.status == GoalStatus.FAILED
    assert "graph explosion" in result.error


# ── _synthesize_goal_tree_results ─────────────────────────────────────────────

async def test_synthesize_no_results_returns_all_failed() -> None:
    text = await _synthesize_goal_tree_results("Goal", [], None)
    assert "failed" in text.lower()


async def test_synthesize_with_provider_none_joins_successes() -> None:
    sub_results = [
        {"goal": "T1", "result": "Result A", "success": True},
        {"goal": "T2", "result": "Result B", "success": False},
    ]
    text = await _synthesize_goal_tree_results("Goal", sub_results, None)
    assert "Result A" in text
    assert "Result B" not in text


async def test_synthesize_with_provider_returns_llm_content() -> None:
    fake = FakeProvider(responses=["Synthesized answer here"])
    sub_results = [
        {"goal": "T1", "result": "R1", "success": True},
        {"goal": "T2", "result": "R2", "success": True},
    ]
    text = await _synthesize_goal_tree_results("The goal", sub_results, fake)
    assert text == "Synthesized answer here"


async def test_synthesize_llm_failure_falls_back_to_join() -> None:
    broken_provider = MagicMock()
    broken_provider.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
    broken_provider._default_model = ""
    sub_results = [
        {"goal": "T1", "result": "Fallback result", "success": True},
    ]
    text = await _synthesize_goal_tree_results("Goal", sub_results, broken_provider)
    assert "Fallback result" in text


async def test_synthesize_all_failed_sub_results_returns_fallback() -> None:
    sub_results = [
        {"goal": "T1", "result": "", "success": False},
    ]
    text = await _synthesize_goal_tree_results("Goal", sub_results, None)
    assert text == "All sub-goals failed."


# ── execute_goal_tree ─────────────────────────────────────────────────────────

async def test_execute_goal_tree_no_decompose_returns_empty() -> None:
    fake = FakeProvider(responses=['{"decompose": false}'])
    result = await execute_goal_tree(
        "Simple goal",
        planner=fake,
        tenant_ctx=_CTX,
        parent_goal_id="p1",
        graph_factory=MagicMock(),
    )
    assert result == []


async def test_execute_goal_tree_with_two_independent_subgoals() -> None:
    """Two independent sub-goals (no deps) should both complete."""
    decomp_response = '{"decompose": true, "sub_goals": [{"id": "sg1", "description": "Task A", "depends_on": []}, {"id": "sg2", "description": "Task B", "depends_on": []}]}'
    synth_response = "Synthesized: Tasks A and B complete"

    fake = FakeProvider(responses=[decomp_response, synth_response])

    completed_state = MagicMock()
    completed_state.status = GoalStatus.COMPLETE
    completed_state.steps = [MagicMock(description="s", output="done")]

    graph = MagicMock()
    graph.run = AsyncMock(return_value=completed_state)
    graph_factory = MagicMock(return_value=graph)

    results = await execute_goal_tree(
        "Complex goal",
        planner=fake,
        tenant_ctx=_CTX,
        parent_goal_id="p1",
        graph_factory=graph_factory,
        max_parallel=4,
    )
    # 2 sub-goals + 1 synthesis = 3 items
    assert len(results) == 3
    assert results[-1].sub_goal_id == "synthesis"
    assert results[-1].status == GoalStatus.COMPLETE


async def test_execute_goal_tree_with_dependency_chain() -> None:
    """sg2 depends on sg1 — they should execute in waves."""
    decomp_response = '{"decompose": true, "sub_goals": [{"id": "sg1", "description": "First", "depends_on": []}, {"id": "sg2", "description": "Second", "depends_on": ["sg1"]}]}'
    synth_response = "Chained result"
    fake = FakeProvider(responses=[decomp_response, synth_response])

    completed_state = MagicMock()
    completed_state.status = GoalStatus.COMPLETE
    completed_state.steps = [MagicMock(description="s", output="done")]

    graph = MagicMock()
    graph.run = AsyncMock(return_value=completed_state)
    graph_factory = MagicMock(return_value=graph)

    results = await execute_goal_tree(
        "Chained goal",
        planner=fake,
        tenant_ctx=_CTX,
        parent_goal_id="p1",
        graph_factory=graph_factory,
    )
    ids = [r.sub_goal_id for r in results]
    assert "sg1" in ids
    assert "sg2" in ids
    assert "synthesis" in ids
    # sg1 must appear before sg2
    assert ids.index("sg1") < ids.index("sg2")


async def test_execute_goal_tree_failed_subgoal_still_synthesizes() -> None:
    decomp_response = '{"decompose": true, "sub_goals": [{"id": "sg1", "description": "Will fail", "depends_on": []}]}'
    fake = FakeProvider(responses=[decomp_response, "Partial synthesis"])

    graph = MagicMock()
    graph.run = AsyncMock(side_effect=RuntimeError("agent crashed"))
    graph_factory = MagicMock(return_value=graph)

    results = await execute_goal_tree(
        "Failing goal",
        planner=fake,
        tenant_ctx=_CTX,
        parent_goal_id="p1",
        graph_factory=graph_factory,
    )
    # Should still include synthesis
    assert results[-1].sub_goal_id == "synthesis"
    assert results[0].status == GoalStatus.FAILED
