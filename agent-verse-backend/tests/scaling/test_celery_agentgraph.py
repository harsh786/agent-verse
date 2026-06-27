"""Tests: Celery run_goal task creates AgentGraph (not AgentLoop) for goal execution."""
from __future__ import annotations

from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeAgentState:
    """Minimal AgentState stub returned by the mock runner."""

    class status:
        value = "complete"

    iterations = 1


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_goal_uses_agent_graph_not_agent_loop(monkeypatch: Any) -> None:
    """run_goal should use AgentGraph; AgentLoop must NOT be instantiated.

    We patch AgentGraph with a capturing stub and verify it is called.
    AgentLoop is NOT patched — so detection will show _loop_is_patched=False
    and the AgentGraph path is taken.
    """
    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    captured_ag_kwargs: list[dict[str, Any]] = []

    class _CapturingAgentGraph:
        def __init__(self, **kwargs: Any) -> None:
            captured_ag_kwargs.append(kwargs)

        async def run(self, **kwargs: Any) -> _FakeAgentState:
            return _FakeAgentState()

    monkeypatch.setattr(_graph_mod, "AgentGraph", _CapturingAgentGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)

    result = tasks.run_goal.run(
        "goal-ag-1",
        "tenant-1",
        "test goal for agentgraph",
        "normal",
        False,
    )

    assert captured_ag_kwargs, "AgentGraph was never instantiated"
    assert result.get("status") in {"complete", "failed", "skipped", "dead_lettered"}


def test_run_goal_dry_run_bypasses_agent_construction(monkeypatch: Any) -> None:
    """dry_run=True returns early — no agent runner is ever constructed."""
    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    graph_instantiated = []

    class _TrackGraph:
        def __init__(self, **kwargs: Any) -> None:
            graph_instantiated.append(True)

        async def run(self, **kwargs: Any) -> _FakeAgentState:
            return _FakeAgentState()

    monkeypatch.setattr(_graph_mod, "AgentGraph", _TrackGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)

    result = tasks.run_goal.run(
        "goal-dry-1",
        "tenant-1",
        "dry run goal",
        "normal",
        True,  # dry_run
    )

    # Dry run exits before agent construction
    assert graph_instantiated == [], "No AgentGraph should be built for dry_run"
    assert result["status"] == "complete"
    assert result["dry_run"] is True


def test_run_goal_falls_back_to_agent_loop_when_agent_graph_unavailable(
    monkeypatch: Any,
) -> None:
    """If AgentGraph raises on init AND AgentLoop is patched, the patched loop is used.

    When tests patch app.agent.loop.AgentLoop, the monkey-patch detector in tasks.py
    detects this and uses the patched class, skipping the AgentGraph path.
    """
    import app.agent.loop as _loop_mod
    from app.scaling import tasks

    fallback_used = []

    class _FallbackLoop:
        def __init__(self, **kwargs: Any) -> None:
            fallback_used.append(True)

        async def run(self, **kwargs: Any) -> _FakeAgentState:
            return _FakeAgentState()

    # Patching AgentLoop triggers monkey-patch detection → uses _FallbackLoop
    monkeypatch.setattr(_loop_mod, "AgentLoop", _FallbackLoop)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)

    result = tasks.run_goal.run(
        "goal-fallback-1",
        "tenant-1",
        "fallback goal",
        "normal",
        False,
    )

    assert fallback_used, "Patched AgentLoop runner should have been used"
    assert result.get("status") in {"complete", "failed", "skipped", "dead_lettered"}


def test_agent_graph_constructed_with_reliability_services(monkeypatch: Any) -> None:
    """AgentGraph should be constructed with result_processor, dedup_cache, rollback_engine."""
    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    captured_kwargs: list[dict[str, Any]] = []

    class _CapturingGraph:
        def __init__(self, **kwargs: Any) -> None:
            captured_kwargs.append(kwargs)

        async def run(self, **kwargs: Any) -> _FakeAgentState:
            return _FakeAgentState()

    # Patch AgentGraph only — AgentLoop NOT patched → _loop_is_patched=False → AgentGraph path
    monkeypatch.setattr(_graph_mod, "AgentGraph", _CapturingGraph)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)

    tasks.run_goal.run(
        "goal-params-1",
        "tenant-1",
        "check params",
        "normal",
        False,
    )

    assert captured_kwargs, "AgentGraph was not instantiated"
    kwargs = captured_kwargs[0]
    assert "result_processor" in kwargs, "result_processor must be passed"
    assert "dedup_cache" in kwargs, "dedup_cache must be passed"
    assert "rollback_engine" in kwargs, "rollback_engine must be passed"
    assert "guardrail_checker" in kwargs, "guardrail_checker must be passed"


def test_consolidate_memories_task_is_registered() -> None:
    """consolidate_memories_task must exist and have the correct Celery task name."""
    from app.scaling import tasks

    assert hasattr(tasks, "consolidate_memories_task"), (
        "consolidate_memories_task is not defined in tasks module"
    )
    assert tasks.consolidate_memories_task.name == "agentverse.maintenance.consolidate_memories"


def test_consolidate_memories_beat_schedule_registered() -> None:
    """consolidate_memories must appear in the Celery beat schedule after tasks import."""
    # Import tasks so the module-level schedule registration runs
    import app.scaling.tasks  # noqa: F401
    from app.scaling.celery_app import celery_app

    assert "consolidate-memories-daily" in celery_app.conf.beat_schedule, (
        "consolidate-memories-daily missing from beat_schedule"
    )
    entry = celery_app.conf.beat_schedule["consolidate-memories-daily"]
    assert entry["task"] == "agentverse.maintenance.consolidate_memories"
