"""Worker (Celery run_goal) path: terminal goals write canonical Reflexion memory."""

from __future__ import annotations

from typing import Any

import pytest

from app.memory.reflexion import ReflexionService
from app.memory.repository import InMemoryMemoryRepository


@pytest.fixture
def worker(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import app.agent.graph as graph_mod
    from app.agent.state import AgentState, GoalStatus, StepResult, StepStatus
    from app.scaling import tasks

    repo = InMemoryMemoryRepository()
    seen: dict[str, Any] = {
        "repo": repo,
        "graph_runs": 0,
        "status": GoalStatus.COMPLETE,
        "service_args": [],
    }

    class _Graph:
        def __init__(self, **kwargs: Any) -> None:
            self._pause_gate: Any = None

        async def run(self, **kwargs: Any) -> Any:
            seen["graph_runs"] += 1
            state = AgentState(
                goal=kwargs["goal"], tenant_ctx=kwargs["tenant_ctx"], goal_id=kwargs["goal_id"]
            )
            state.status = seen["status"]
            state.iterations = 1
            state.steps = [
                StepResult(step_id="w1", description="Collect metrics", status=StepStatus.COMPLETE)
            ]
            if state.status is GoalStatus.FAILED:
                state.error_message = "rate limit 429 from metrics API"
            return state

    def _service(db_factory: Any, embedder: Any) -> ReflexionService:
        seen["service_args"].append((db_factory, embedder))
        return ReflexionService(repository=repo)

    class _NoTenantRules:
        """The tenant's persisted guardrail rules (none). run_goal binds the
        Postgres rule store when nothing is bound (P8-1); this suite has no
        database, so it binds an empty store first."""

        async def load(self, tenant_id: str) -> list[Any]:
            return []

    from app.guardrails_v2.engine import guardrails_engine

    guardrails_engine.bind_repository(_NoTenantRules())  # restored by tests/conftest.py
    monkeypatch.setattr(graph_mod, "AgentGraph", _Graph)
    monkeypatch.setattr(tasks, "_worker_reflexion_service", _service)
    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "")
    monkeypatch.setattr(tasks, "_get_sync_redis", lambda: None)
    monkeypatch.setenv("ENVIRONMENT", "development")
    return seen


def test_completed_worker_goal_writes_a_memory_record(worker: dict[str, Any]) -> None:
    from app.scaling import tasks

    result = tasks.run_goal.run("gwl1", "tenant-wl", "collect weekly metrics", "normal", False)

    assert result["status"] == "complete"
    assert worker["graph_runs"] == 1
    records = tasks._run_async(worker["repo"].list_records("tenant-wl"))
    assert len(records) == 1
    assert records[0].source_goal_id == "gwl1"
    assert records[0].memory_kind == "reflexion"
    assert "goal://gwl1/outcome/complete" in records[0].evidence_refs


def test_failed_worker_goal_writes_a_failure_lesson(worker: dict[str, Any]) -> None:
    from app.agent.state import GoalStatus
    from app.scaling import tasks

    worker["status"] = GoalStatus.FAILED
    result = tasks.run_goal.run("gwl2", "tenant-wl", "collect weekly metrics", "normal", False)

    assert result["status"] == "failed"
    (record,) = tasks._run_async(worker["repo"].list_records("tenant-wl"))
    assert "failed (rate_limit)" in record.safe_summary


def test_dry_run_worker_goal_writes_nothing(worker: dict[str, Any]) -> None:
    from app.scaling import tasks

    result = tasks.run_goal.run("gwl3", "tenant-wl", "collect weekly metrics", "normal", True)

    assert result["dry_run"] is True
    assert tasks._run_async(worker["repo"].list_records("tenant-wl")) == ()


async def test_worker_learning_helper_never_writes_for_dry_runs() -> None:
    from app.agent.state import AgentState, GoalStatus
    from app.scaling import tasks
    from app.tenancy.context import PlanTier, TenantContext

    repo = InMemoryMemoryRepository()
    ctx = TenantContext(tenant_id="tenant-wl", plan=PlanTier.FREE, api_key_id="k")
    state = AgentState(goal="g", tenant_ctx=ctx, goal_id="gwl4")
    state.status = GoalStatus.COMPLETE
    result = await tasks._learn_from_worker_goal(
        ReflexionService(repository=repo),
        state,
        tenant_id="tenant-wl",
        goal_id="gwl4",
        dry_run=True,
        agent_id=None,
    )
    assert result.status == "skipped"
    assert await repo.list_records("tenant-wl") == ()


def test_worker_reflexion_service_needs_a_database() -> None:
    from app.scaling import tasks

    assert tasks._worker_reflexion_service(None, None) is None


def test_failed_worker_goal_stores_its_reason_on_the_goal_row(
    worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """GAP-WORKER: 338 of 342 'Required retrieval failed' goals had an empty
    goals.error_message - the reason only reached the goal_failed event."""
    from app.agent.state import GoalStatus
    from app.scaling import tasks
    from app.services.goal_service import GoalService

    writes: list[tuple[str, str]] = []

    async def _update(self: Any, goal_id: str, tenant_id: str, status: str, **k: Any) -> bool:
        writes.append((status, str(k.get("error_message", ""))))
        return True

    monkeypatch.setattr(GoalService, "_db_update_goal_status", _update)
    worker["status"] = GoalStatus.FAILED
    result = tasks.run_goal.run("gwl5", "tenant-wl", "collect weekly metrics", "normal", False)

    assert result["status"] == "failed"
    assert ("failed", "rate limit 429 from metrics API") in writes


def test_completed_worker_goal_writes_no_error_message(
    worker: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.scaling import tasks
    from app.services.goal_service import GoalService

    writes: list[tuple[str, str]] = []

    async def _update(self: Any, goal_id: str, tenant_id: str, status: str, **k: Any) -> bool:
        writes.append((status, str(k.get("error_message", ""))))
        return True

    monkeypatch.setattr(GoalService, "_db_update_goal_status", _update)
    tasks.run_goal.run("gwl6", "tenant-wl", "collect weekly metrics", "normal", False)

    assert ("complete", "") in writes
