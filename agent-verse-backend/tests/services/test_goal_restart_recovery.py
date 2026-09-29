"""Restart recovery only re-runs goals whose in-process runner is provably gone.

Regression: ``_recover_interrupted_goals`` ran on EVERY API replica start and
re-enqueued every unfinished goal with no local task — including goals running
on Celery workers (duplicate execution) — to the free queue regardless of the
tenant's plan, and several replicas starting together all re-enqueued the same
goals.

Now each goal records its runner (``execution_context.runner``): worker goals
belong to Celery (redelivery + the stuck-goal sweeper) and are never touched;
an in-process goal is recovered only when its replica's Redis heartbeat is gone
and no worker holds its execution lock, and only by the replica that wins an
atomic claim on the row. Recovered goals go to the tenant's real plan queue.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis
import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import _RUNNER_KEY, GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

TENANT = "t-recover"


class _GoalsTable:
    """Shared goals rows + the atomic runner claim (UPDATE ... WHERE ... RETURNING)."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.plans: dict[str, str] = {TENANT: "enterprise"}

    def add(self, goal_id: str, status: str, runner: dict[str, Any] | None) -> None:
        ctx: dict[str, Any] = {}
        if runner is not None:
            ctx[_RUNNER_KEY] = runner
        self.rows[goal_id] = {"status": status, "execution_context": ctx}

    def bind(self, svc: GoalService) -> GoalService:
        table = self
        svc._db = object()

        async def _claim(goal_id: str, tenant_id: str, *, expected_replica: str,
                         runner: dict[str, Any]) -> bool:
            row = table.rows.get(goal_id)
            if row is None or row["status"] in {"complete", "failed", "cancelled",
                                                "waiting_human"}:
                return False
            current = row["execution_context"].get(_RUNNER_KEY) or {}
            if current.get("replica") != expected_replica:
                return False
            row["execution_context"][_RUNNER_KEY] = runner
            return True

        async def _plan(tenant_id: str) -> str | None:
            return table.plans.get(tenant_id)

        async def _update(goal_id: str, tenant_id: str, status: str, error_message: str = "",
                          iterations: int = 0, only_if_active: bool = False) -> None:
            table.rows[goal_id]["status"] = status

        svc._db_claim_goal_runner = _claim  # type: ignore[method-assign]
        svc._tenant_plan = _plan  # type: ignore[method-assign]
        svc._db_update_goal_status = _update  # type: ignore[method-assign]
        return svc

    def load_into(self, svc: GoalService) -> None:
        """What sync_from_db warms into memory."""
        for goal_id, row in self.rows.items():
            svc._goals[goal_id] = GoalRecord(
                goal_id=goal_id, goal_text=f"text {goal_id}", status=GoalStatus(row["status"]),
                tenant_id=TENANT, priority="high", dry_run=False, created_at="",
                agent_id="agent-1", execution_context=dict(row["execution_context"]),
            )


def _replica(table: _GoalsTable, redis: Any, queue: Any) -> GoalService:
    svc = table.bind(GoalService(task_queue=queue))
    svc._redis = redis
    table.load_into(svc)
    return svc


@pytest.fixture
def redis() -> Any:
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_worker_owned_goals_are_never_re_enqueued(redis: Any) -> None:
    table = _GoalsTable()
    table.add("g-worker", "executing", {"kind": "worker"})
    table.add("g-legacy", "executing", None)  # no recorded owner: not provably orphaned
    queue = MagicMock()
    svc = _replica(table, redis, queue)

    assert await svc._recover_interrupted_goals() == 0
    queue.enqueue_goal.assert_not_called()
    assert table.rows["g-worker"]["status"] == "executing"


@pytest.mark.asyncio
async def test_goal_of_a_live_replica_is_left_alone(redis: Any) -> None:
    table = _GoalsTable()
    table.add("g-live", "executing", {"kind": "in_process", "replica": "replica-a"})
    await redis.set("goal_replica_alive:replica-a", "1", ex=30)
    queue = MagicMock()

    assert await _replica(table, redis, queue)._recover_interrupted_goals() == 0
    queue.enqueue_goal.assert_not_called()


@pytest.mark.asyncio
async def test_goal_with_a_live_worker_lock_is_left_alone(redis: Any) -> None:
    table = _GoalsTable()
    table.add("g-locked", "executing", {"kind": "in_process", "replica": "replica-dead"})
    await redis.set("goal_lock:g-locked", "worker-x")
    queue = MagicMock()

    assert await _replica(table, redis, queue)._recover_interrupted_goals() == 0
    queue.enqueue_goal.assert_not_called()


@pytest.mark.asyncio
async def test_orphan_of_a_dead_replica_goes_to_the_tenant_plan_queue(redis: Any) -> None:
    table = _GoalsTable()
    table.add("g-orphan", "executing", {"kind": "in_process", "replica": "replica-dead"})
    queue = MagicMock()
    svc = _replica(table, redis, queue)

    assert await svc._recover_interrupted_goals() == 1
    queue.enqueue_goal.assert_called_once()
    kwargs = queue.enqueue_goal.call_args.kwargs
    assert kwargs["plan"] == "enterprise", "recovered goals keep the tenant's plan queue"
    assert kwargs["goal_id"] == "g-orphan" and kwargs["agent_id"] == "agent-1"
    assert kwargs["priority"] == "high"
    assert table.rows["g-orphan"]["execution_context"][_RUNNER_KEY]["kind"] == "worker"


@pytest.mark.asyncio
async def test_replicas_starting_together_recover_an_orphan_once(redis: Any) -> None:
    import asyncio

    table = _GoalsTable()
    table.add("g-orphan", "planning", {"kind": "in_process", "replica": "replica-dead"})
    queue = MagicMock()
    replicas = [_replica(table, redis, queue) for _ in range(3)]

    counts = await asyncio.gather(*(r._recover_interrupted_goals() for r in replicas))

    assert sum(counts) == 1
    queue.enqueue_goal.assert_called_once()


@pytest.mark.asyncio
async def test_unknown_plan_skips_instead_of_defaulting_to_free(redis: Any) -> None:
    table = _GoalsTable()
    table.plans.clear()
    table.add("g-orphan", "executing", {"kind": "in_process", "replica": "replica-dead"})
    queue = MagicMock()

    assert await _replica(table, redis, queue)._recover_interrupted_goals() == 0
    queue.enqueue_goal.assert_not_called()
    runner = table.rows["g-orphan"]["execution_context"][_RUNNER_KEY]
    assert runner["replica"] == "replica-dead", "not claimed when it cannot be routed"


@pytest.mark.asyncio
async def test_without_redis_nothing_is_provably_orphaned() -> None:
    table = _GoalsTable()
    table.add("g-orphan", "executing", {"kind": "in_process", "replica": "replica-dead"})
    queue = MagicMock()
    svc = _replica(table, None, queue)

    assert await svc._recover_interrupted_goals() == 0
    queue.enqueue_goal.assert_not_called()
    assert svc._goals["g-orphan"].status == GoalStatus.EXECUTING


@pytest.mark.asyncio
async def test_orphan_without_a_task_queue_is_failed_durably(redis: Any) -> None:
    table = _GoalsTable()
    table.add("g-orphan", "executing", {"kind": "in_process", "replica": "replica-dead"})
    svc = _replica(table, redis, None)

    with patch("app.tenancy.limits.decrement_concurrent_goals", AsyncMock()) as dec:
        assert await svc._recover_interrupted_goals() == 0
    assert table.rows["g-orphan"]["status"] == "failed"
    assert svc._goals["g-orphan"].status == GoalStatus.FAILED
    dec.assert_awaited()  # the dead replica's concurrency slot is released


@pytest.mark.asyncio
async def test_submit_records_the_runner_and_heartbeat(redis: Any) -> None:
    ctx = TenantContext(tenant_id=TENANT, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    svc = GoalService(task_queue=MagicMock())
    svc._redis = redis
    from app.services.dedup import GoalDeduplicator

    with (
        patch("app.tenancy.limits.check_and_increment_concurrent_goals", AsyncMock()),
        patch("app.services.dedup._default_deduplicator", GoalDeduplicator()),
    ):
        queued = await svc.submit_goal(goal="a", priority="normal", dry_run=False, tenant_ctx=ctx)
    assert svc._goals[queued["goal_id"]].execution_context[_RUNNER_KEY] == {"kind": "worker"}

    inproc = GoalService()
    inproc._redis = redis
    with (
        patch("app.tenancy.limits.check_and_increment_concurrent_goals", AsyncMock()),
        patch("app.services.dedup._default_deduplicator", GoalDeduplicator()),
        patch.object(inproc, "_build_tool_context", AsyncMock(return_value=None)),
        patch.object(inproc, "_run_agent_loop", AsyncMock()),
    ):
        local = await inproc.submit_goal(goal="b", priority="normal", dry_run=False,
                                         tenant_ctx=ctx)
    runner = inproc._goals[local["goal_id"]].execution_context[_RUNNER_KEY]
    assert runner == {"kind": "in_process", "replica": inproc._replica_id}
    assert await redis.exists(f"goal_replica_alive:{inproc._replica_id}")
    inproc.stop_replica_heartbeat()


def test_sync_from_db_no_longer_recovers_before_redis_is_wired() -> None:
    import inspect

    import app.main as main_mod
    from app.services import goal_service

    assert "_recover_interrupted_goals" not in inspect.getsource(GoalService.sync_from_db)
    assert "recover_interrupted_goals(" in inspect.getsource(main_mod)
    assert goal_service is not None
