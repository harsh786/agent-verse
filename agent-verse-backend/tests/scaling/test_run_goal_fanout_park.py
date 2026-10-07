"""a01-F006-05: a fan-out parent releases its worker slot instead of waiting.

When the graph ends ``waiting_children`` (its sub-goals dispatched as real
goals), run_goal records no terminal status, keeps the tenant concurrency slot
(the sub-goals run under it), releases its execution lock BEFORE the park is
visible (the last child's wake may re-enqueue the parent at once) and parks the
row. A stale / redelivered message never claims a parked parent, and every
sub-goal run that ends tries to re-queue its parent.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.agent.state import AgentState, GoalStatus
from app.scaling.tasks import _claim_goal_for_execution as _real_claim
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.usefixtures("readable_emergency_stop")


def _graph_ending(status: GoalStatus, **context: Any) -> type:
    class _Graph:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, **kwargs: Any) -> Any:
            st = AgentState(
                goal="fan out",
                tenant_ctx=TenantContext(tenant_id="tenant-1", plan=PlanTier.FREE,
                                         api_key_id="k"),
                goal_id=kwargs.get("goal_id") or "",
            )
            st.status = status
            st.context.update(context)
            return st

    return _Graph


@pytest.fixture
def spies(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.scaling import tasks
    from app.services.goal_service import GoalService

    rec: dict[str, list[Any]] = {
        "order": [], "status": [], "decrement": [], "finalize": [], "park": [], "wake": [],
        "events": [],
    }

    class _Lock:
        def __init__(self, *a: Any) -> None:
            pass

        token = "tok"

        def acquire(self, goal_id: str, ttl_ms: int = 0) -> bool:
            return True

        def release(self, goal_id: str) -> None:
            rec["order"].append(("release", goal_id))

    async def fake_update(self: Any, goal_id: str, tenant_id: str, status: str, **kw: Any) -> bool:
        rec["status"].append(status)
        return True

    async def fake_decrement(tenant_id: str, redis_url: str, *a: Any) -> None:
        rec["decrement"].append(tenant_id)

    async def fake_finalize(goal_id: str, tenant_id: str) -> None:
        rec["finalize"].append(goal_id)

    async def fake_park(goal_id: str, tenant_id: str, **kw: Any) -> str:
        rec["order"].append(("park", goal_id))
        rec["park"].append(kw)
        return "parked"

    async def fake_wake(goal_id: str, tenant_id: str) -> bool:
        rec["order"].append(("wake", goal_id))
        rec["wake"].append(goal_id)
        return False

    async def fake_ctx(goal_id: str, tenant_id: str) -> dict[str, Any] | None:
        return None

    monkeypatch.setattr(tasks, "_get_llm_provider", lambda tenant_id: None)
    monkeypatch.setattr(tasks, "_SyncGoalLock", _Lock)
    monkeypatch.setattr(tasks.celery_app.conf, "broker_url", "redis://localhost:6399/0")
    monkeypatch.setattr(tasks, "_goal_lock_client", lambda url: object())
    monkeypatch.setattr(GoalService, "_db_update_goal_status", fake_update)
    monkeypatch.setattr(tasks, "_subgoal_context", fake_ctx)
    with (
        patch.object(tasks, "_finalize_owning_mission", fake_finalize),
        patch.object(tasks, "_decrement_after_completion", fake_decrement),
        patch.object(tasks, "_park_fanout_parent", fake_park),
        patch.object(tasks, "_wake_fanout_parent_of", fake_wake),
    ):
        yield rec


def _run(goal_id: str, **kw: Any) -> Any:
    from app.scaling import tasks

    tasks.run_goal.push_request(retries=0, called_directly=False)
    try:
        return tasks.run_goal.run(goal_id, "tenant-1", "fan out", connector_ids=["c1"], **kw)
    finally:
        tasks.run_goal.pop_request()


def test_parked_parent_releases_its_worker_and_keeps_its_slot(
    monkeypatch: pytest.MonkeyPatch, spies: dict[str, list[Any]]
) -> None:
    import app.agent.graph as _graph_mod

    monkeypatch.setattr(
        _graph_mod,
        "AgentGraph",
        _graph_ending(GoalStatus.WAITING_CHILDREN, fanout_parked="supervisor"),
    )

    result = _run("goal-parent-1")

    assert result["status"] == "waiting_children"
    assert result["park"] == "parked"
    # No terminal bookkeeping: not complete / failed, no slot release, no mission.
    assert not {"complete", "failed", "waiting_children"} & set(spies["status"])
    assert spies["decrement"] == []
    assert spies["finalize"] == []
    # The lock is released BEFORE the park becomes visible.
    order = spies["order"]
    assert order.index(("release", "goal-parent-1")) < order.index(("park", "goal-parent-1"))
    (park,) = spies["park"]
    assert park["kind"] == "supervisor"
    assert park["plan"] == "free"
    assert park["connector_ids"] == ["c1"]
    # A parent is not a sub-goal: it never wakes anybody itself.
    assert spies["wake"] == []


def test_a_sub_goal_run_that_ends_tries_to_wake_its_parent(
    monkeypatch: pytest.MonkeyPatch, spies: dict[str, list[Any]]
) -> None:
    import app.agent.graph as _graph_mod

    monkeypatch.setattr(_graph_mod, "AgentGraph", _graph_ending(GoalStatus.COMPLETE))

    result = _run("goal-child-1", subgoal=True)

    assert result["status"] == "complete"
    assert "complete" in spies["status"]
    assert spies["wake"] == ["goal-child-1"]
    # The wake runs after the child's terminal status was written.
    assert spies["order"][-1] == ("wake", "goal-child-1")
    # A sub-goal holds no slot of its own (it shares its parent's).
    assert spies["park"] == []


def test_a_redelivered_message_never_claims_a_parked_parent(
    monkeypatch: pytest.MonkeyPatch, spies: dict[str, list[Any]]
) -> None:
    from app.scaling import tasks

    async def _claim(goal_id: str, tenant_id: str) -> str:
        return "waiting_children"

    def _provider(tenant_id: str) -> Any:
        raise AssertionError("a parked parent must not run")

    monkeypatch.setattr(tasks, "_claim_goal_for_execution", _claim)
    monkeypatch.setattr(tasks, "_get_llm_provider", _provider)

    result = _run("goal-parked-1")

    assert result == {
        "status": "skipped",
        "goal_id": "goal-parked-1",
        "reason": "waiting_for_children",
        "goal_status": "waiting_children",
    }
    assert spies["decrement"] == []
    assert "executing" not in spies["status"]


@pytest.mark.asyncio
async def test_claim_sql_excludes_waiting_children(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.scaling import tasks
    from tests.scaling.test_run_goal_claim_waiting_human import _Session

    monkeypatch.setattr(tasks, "_claim_goal_for_execution", _real_claim)
    session = _Session(claimed=None, existing="waiting_children")
    monkeypatch.setattr("app.db.session.get_session_factory", lambda: lambda: session)

    assert await tasks._claim_goal_for_execution("g", "t") == "waiting_children"
    (update,) = session.updates
    assert "'waiting_children'" in update and "'waiting_human'" in update


def test_error_after_park_is_not_retried_and_keeps_the_slot(
    monkeypatch: pytest.MonkeyPatch, spies: dict[str, list[Any]]
) -> None:
    import app.agent.graph as _graph_mod
    from app.scaling import tasks

    retried: list[Any] = []

    async def _boom(goal_id: str, tenant_id: str, **kw: Any) -> str:
        raise ConnectionError("db went away mid-park")

    def _retry(*a: Any, **k: Any) -> Any:
        retried.append(k)
        raise RuntimeError("RETRY")

    monkeypatch.setattr(
        _graph_mod, "AgentGraph", _graph_ending(GoalStatus.WAITING_CHILDREN)
    )
    monkeypatch.setattr(tasks.run_goal, "retry", _retry)
    with patch.object(tasks, "_park_fanout_parent", _boom):
        result = _run("goal-parent-2")

    assert retried == []
    assert result["status"] == "waiting_children"
    assert result["retryable"] is False
    assert spies["decrement"] == []
