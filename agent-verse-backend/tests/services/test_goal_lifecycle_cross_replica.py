"""Goal lifecycle actions work from any replica, not just the one that created the goal.

Regression: cancel / pause / resume / approve looked the goal up only in the
replica's own memory, so every other replica — and every goal run by a Celery
worker — answered 404. They now load the goal from Postgres (tenant-scoped,
under RLS) and deliver the control signal through Redis, which the owning
runner (another replica's in-process loop or a worker) observes.

Two GoalService instances share one fake goals table and one (fake)redis server.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import fakeredis.aioredis
import pytest

from app.agent.state import AgentState, GoalStatus
from app.core.errors import NotFoundError, ServiceUnavailableError
from app.reliability.goal_lifecycle import is_cancelled_sync, is_paused_sync
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-xr", plan=PlanTier.PROFESSIONAL, api_key_id="k")
OTHER = TenantContext(tenant_id="t-other", plan=PlanTier.PROFESSIONAL, api_key_id="k2")


class SharedGoalsTable:
    """Stand-in for the ``goals`` table, filtered by tenant like RLS would."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def bind(self, svc: GoalService) -> GoalService:
        table = self
        svc._db = object()  # "a DB is configured" — every access goes through the fakes

        async def _persist(goal_id: str, tenant_id: str, goal_text: str, status: str,
                           priority: str, dry_run: bool, agent_id: str | None = None,
                           workflow_mode: str = "single_agent",
                           execution_context: dict[str, Any] | None = None,
                           raise_on_error: bool = False,
                           runtime_profile_columns: dict[str, Any] | None = None) -> None:
            table.rows[goal_id] = {
                "id": goal_id, "tenant_id": tenant_id, "goal_text": goal_text,
                "status": status, "priority": priority, "dry_run": dry_run,
                "agent_id": agent_id, "workflow_mode": workflow_mode,
                "execution_context": dict(execution_context or {}),
            }

        async def _get(goal_id: str, tenant_ctx: TenantContext) -> GoalRecord | None:
            row = table.rows.get(goal_id)
            if row is None or row["tenant_id"] != tenant_ctx.tenant_id:
                return None
            record = GoalRecord(
                goal_id=row["id"], goal_text=row["goal_text"],
                status=GoalStatus(row["status"]), tenant_id=row["tenant_id"],
                priority=row["priority"], dry_run=row["dry_run"], created_at="",
                agent_id=row["agent_id"], workflow_mode=row["workflow_mode"],
                execution_context=dict(row["execution_context"]),
            )
            previous = svc._goals.get(goal_id)
            if previous is not None and previous.tenant_id == record.tenant_id:
                record.subscribers = previous.subscribers
                record.task = previous.task
            svc._goals[goal_id] = record
            return record

        async def _update(goal_id: str, tenant_id: str, status: str,
                          error_message: str = "", iterations: int = 0) -> None:
            row = table.rows.get(goal_id)
            if row is not None and row["tenant_id"] == tenant_id:
                row["status"] = status

        async def _suspended(goal_id: str, tenant_id: str, suspended: bool) -> None:
            row = table.rows.get(goal_id)
            if row is None or row["tenant_id"] != tenant_id:
                return
            if suspended:
                row["execution_context"]["_suspended_for_approval"] = True
            else:
                row["execution_context"].pop("_suspended_for_approval", None)

        svc._db_persist_goal = _persist  # type: ignore[method-assign]
        svc._db_get_goal_record = _get  # type: ignore[method-assign]
        svc._db_update_goal_status = _update  # type: ignore[method-assign]
        svc._db_set_suspended = _suspended  # type: ignore[method-assign]
        return svc


@pytest.fixture
def cluster() -> tuple[SharedGoalsTable, Any, Any]:
    server = fakeredis.FakeServer()
    return (
        SharedGoalsTable(),
        fakeredis.aioredis.FakeRedis(server=server, decode_responses=True),
        fakeredis.FakeRedis(server=server, decode_responses=True),
    )


def _replica(table: SharedGoalsTable, redis: Any, *, task_queue: Any = None) -> GoalService:
    svc = table.bind(GoalService(task_queue=task_queue))
    svc._redis = redis
    return svc


async def _submit_worker_goal(replica: GoalService) -> str:
    from app.services.dedup import GoalDeduplicator

    with (
        patch("app.tenancy.limits.check_and_increment_concurrent_goals", AsyncMock()),
        patch("app.services.dedup._default_deduplicator", GoalDeduplicator()),
    ):
        result = await replica.submit_goal(
            goal="rotate the credentials", priority="normal", dry_run=False, tenant_ctx=CTX
        )
    return str(result["goal_id"])


@pytest.mark.asyncio
async def test_worker_goal_created_on_a_is_cancelled_from_b(cluster: Any) -> None:
    table, aredis, sredis = cluster
    replica_a = _replica(table, aredis, task_queue=MagicMock())
    replica_b = _replica(table, aredis, task_queue=MagicMock())
    goal_id = await _submit_worker_goal(replica_a)
    assert goal_id not in replica_b._goals

    with patch("app.tenancy.limits.decrement_concurrent_goals", AsyncMock()):
        result = await replica_b.cancel_goal(goal_id, CTX)

    assert result["status"] == "cancelled"
    assert table.rows[goal_id]["status"] == "cancelled"
    assert is_cancelled_sync(goal_id, sredis), "the worker's cancel flag must be set"


@pytest.mark.asyncio
async def test_worker_goal_is_paused_and_resumed_from_other_replicas(cluster: Any) -> None:
    table, aredis, sredis = cluster
    replica_a = _replica(table, aredis, task_queue=MagicMock())
    replica_b = _replica(table, aredis, task_queue=MagicMock())
    replica_c = _replica(table, aredis, task_queue=MagicMock())
    goal_id = await _submit_worker_goal(replica_a)
    table.rows[goal_id]["status"] = "executing"  # the worker picked it up

    paused = await replica_b.pause_goal(goal_id, CTX)
    assert paused["status"] == "paused"
    assert table.rows[goal_id]["status"] == "waiting_human"
    assert is_paused_sync(goal_id, sredis)

    resumed = await replica_c.resume_goal(goal_id, CTX)
    assert resumed["status"] == "resumed"
    assert table.rows[goal_id]["status"] == "executing"
    assert not is_paused_sync(goal_id, sredis), "resume must clear the worker's pause flag"


@pytest.mark.asyncio
async def test_lifecycle_actions_stay_tenant_scoped(cluster: Any) -> None:
    table, aredis, _ = cluster
    replica_a = _replica(table, aredis, task_queue=MagicMock())
    replica_b = _replica(table, aredis, task_queue=MagicMock())
    goal_id = await _submit_worker_goal(replica_a)

    for action in (replica_b.cancel_goal, replica_b.pause_goal, replica_b.resume_goal):
        with pytest.raises(NotFoundError):
            await action(goal_id, OTHER)
    with pytest.raises(NotFoundError):
        await replica_b.cancel_goal("no-such-goal", CTX)
    assert table.rows[goal_id]["status"] == "planning"


@pytest.mark.asyncio
async def test_remote_cancel_fails_closed_when_the_signal_cannot_be_delivered(
    cluster: Any,
) -> None:
    table, aredis, _ = cluster
    replica_a = _replica(table, aredis, task_queue=MagicMock())
    goal_id = await _submit_worker_goal(replica_a)
    broken = MagicMock()
    broken.set = AsyncMock(side_effect=ConnectionError("redis down"))
    broken.publish = AsyncMock(side_effect=ConnectionError("redis down"))
    replica_b = _replica(table, broken, task_queue=MagicMock())

    with pytest.raises(ServiceUnavailableError):
        await replica_b.cancel_goal(goal_id, CTX)
    assert table.rows[goal_id]["status"] == "planning", "no half-applied cancel"


@pytest.mark.asyncio
async def test_approve_on_another_replica_finds_the_goal(cluster: Any) -> None:
    table, aredis, _ = cluster
    replica_a = _replica(table, aredis, task_queue=MagicMock())
    goal_id = await _submit_worker_goal(replica_a)
    hitl = MagicMock()
    hitl.approve_async = AsyncMock(return_value=True)
    replica_b = _replica(table, aredis, task_queue=MagicMock())
    replica_b._hitl = hitl

    result = await replica_b.handle_approval(goal_id, "req-1", "approve", "op", "", CTX)

    assert result["accepted"] is True
    hitl.approve_async.assert_awaited_once()


class _SteppingLoop:
    """An in-process agent loop that honours the pause gate between two steps."""

    def __init__(self) -> None:
        self._pause_gate: Any = None
        self.steps: list[str] = []
        self.first_step_done = asyncio.Event()

    async def run(self, *, goal: str, tenant_ctx: Any, goal_id: str, **_: Any) -> AgentState:
        for step in ("one", "two"):
            if self._pause_gate is not None:
                await self._pause_gate()
            self.steps.append(step)
            self.first_step_done.set()
            await asyncio.sleep(0.05)
        st = AgentState(goal=goal, tenant_ctx=tenant_ctx)
        st.goal_id = goal_id
        st.status = GoalStatus.COMPLETE
        return st


async def _start_in_process_goal(replica: GoalService, loop: _SteppingLoop) -> str:
    replica._goals["g-inproc"] = GoalRecord(
        goal_id="g-inproc", goal_text="t", status=GoalStatus.EXECUTING,
        tenant_id=CTX.tenant_id, priority="normal", dry_run=False, created_at="",
    )
    await replica._db_persist_goal("g-inproc", CTX.tenant_id, "t", "executing", "normal", False)
    replica._make_agent_loop_for_tenant = lambda *a, **k: loop  # type: ignore[method-assign]
    replica._resolve_tenant_llm_config = AsyncMock(return_value=None)  # type: ignore[method-assign]
    replica._goals["g-inproc"].task = asyncio.create_task(
        replica._run_agent_loop(
            "g-inproc", "t", CTX,
            tool_context=MagicMock(to_prompt_block=MagicMock(return_value="")),
        )
    )
    return "g-inproc"


@pytest.mark.asyncio
async def test_in_process_goal_on_a_is_paused_and_resumed_from_b(
    cluster: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.goal_service as gs_mod

    monkeypatch.setattr(gs_mod, "_PAUSE_POLL_SECONDS", 0.02)
    table, aredis, _ = cluster
    replica_a = _replica(table, aredis)
    replica_b = _replica(table, aredis)
    loop = _SteppingLoop()
    goal_id = await _start_in_process_goal(replica_a, loop)
    await asyncio.wait_for(loop.first_step_done.wait(), timeout=5)

    await replica_b.pause_goal(goal_id, CTX)
    await asyncio.sleep(0.3)
    assert loop.steps == ["one"], "replica A's loop must stop at the next step boundary"
    types_on_a = [e.get("type") for e in replica_a._goals[goal_id].events]
    assert "goal_paused_at_step_boundary" in types_on_a

    await replica_b.resume_goal(goal_id, CTX)
    await asyncio.wait_for(replica_a._goals[goal_id].task, timeout=5)  # type: ignore[arg-type]
    assert loop.steps == ["one", "two"]


@pytest.mark.asyncio
async def test_in_process_goal_on_a_is_cancelled_from_b(
    cluster: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.services.goal_service as gs_mod

    monkeypatch.setattr(gs_mod, "_PAUSE_POLL_SECONDS", 0.02)
    table, aredis, _ = cluster
    replica_a = _replica(table, aredis)
    replica_b = _replica(table, aredis)
    loop = _SteppingLoop()
    goal_id = await _start_in_process_goal(replica_a, loop)
    await asyncio.wait_for(loop.first_step_done.wait(), timeout=5)

    with patch("app.tenancy.limits.decrement_concurrent_goals", AsyncMock()):
        await replica_b.cancel_goal(goal_id, CTX)
        await asyncio.wait_for(replica_a._goals[goal_id].task, timeout=5)  # type: ignore[arg-type]

    assert loop.steps == ["one"], "step two must not run after a cancel"
    assert replica_a._goals[goal_id].status == GoalStatus.CANCELLED
    assert table.rows[goal_id]["status"] == "cancelled"
    assert "goal_failed" not in [e.get("type") for e in replica_a._goals[goal_id].events]
