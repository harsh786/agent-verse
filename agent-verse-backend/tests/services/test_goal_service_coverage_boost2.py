"""Further real-scenario coverage for app/services/goal_service.py.

Targets specific branches identified via
``--cov=app.services.goal_service --cov-report=term-missing`` that remained
uncovered after ``test_goal_service_coverage_boost.py``:

  - ``_check_budget_preflight``: Redis-backed and in-memory budget-exhausted
    rejection paths, and re-raising a ``PlanLimitExceededError`` surfaced by
    the Redis cost controller itself (fail-closed on an explicit signal,
    fail-open on any other error).
  - ``_check_daily_goal_limit_redis``: the atomic INCR-then-validate path,
    including the rollback DECR when the tenant is over the daily limit.
  - ``_recover_interrupted_goals``: the per-goal re-enqueue failure path
    (a broken task queue must not abort recovery of the remaining goals).
  - ``cancel_goal``: cross-replica cancellation signal via Redis pub/sub.
  - ``resume_goal``: an exception raised while dispatching the checkpoint
    "resumed" event must fall back to the legacy pause-event resume path
    instead of leaving the goal stuck.
  - ``subscribe_events`` (SSE): reconnect-with-cursor duplicate suppression,
    end-of-stream sentinel encountered during buffered replay, live event
    delivery once the buffered queue has drained, and a status flip to
    terminal (discovered via a DB refresh) ending the stream without ever
    opening a live queue wait.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.limits import PlanLimitExceededError


def _ctx(tenant_id: str = "cb2-t1", plan: PlanTier = PlanTier.FREE) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, plan=plan, api_key_id="k1")


def _svc() -> GoalService:
    return GoalService()


def _inject_goal(
    svc: GoalService,
    goal_id: str = "g1",
    tenant_id: str = "cb2-t1",
    status: GoalStatus = GoalStatus.EXECUTING,
) -> GoalRecord:
    record = GoalRecord(
        goal_id=goal_id,
        goal_text="do something",
        status=status,
        tenant_id=tenant_id,
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
    )
    svc._goals[goal_id] = record
    return record


# ── _check_budget_preflight ────────────────────────────────────────────────


class _AppState:
    """Minimal stand-in for FastAPI app.state (plain namespace, not Starlette)."""


@pytest.mark.asyncio
async def test_budget_preflight_redis_controller_rejects_when_exhausted() -> None:
    """Cross-replica Redis cost controller reports zero remaining -> reject."""
    svc = _svc()
    state = _AppState()
    state.redis_cost_controller = AsyncMock()
    state.redis_cost_controller.get_budget_status = AsyncMock(
        return_value={"daily_remaining": 0.0}
    )
    svc._app_state = state

    with pytest.raises(PlanLimitExceededError, match="budget exhausted"):
        await svc._check_budget_preflight(_ctx())


@pytest.mark.asyncio
async def test_budget_preflight_redis_controller_allows_when_remaining() -> None:
    """Positive remaining budget must not raise."""
    svc = _svc()
    state = _AppState()
    state.redis_cost_controller = AsyncMock()
    state.redis_cost_controller.get_budget_status = AsyncMock(
        return_value={"daily_remaining": 12.5}
    )
    svc._app_state = state

    await svc._check_budget_preflight(_ctx())  # must not raise


@pytest.mark.asyncio
async def test_budget_preflight_memory_controller_rejects_when_exhausted() -> None:
    """Single-process fallback: in-memory cost controller reports no budget."""
    svc = _svc()
    state = _AppState()
    state.redis_cost_controller = None
    state.cost_controller = MagicMock()
    state.cost_controller.has_remaining_budget = MagicMock(return_value=False)
    svc._app_state = state

    with pytest.raises(PlanLimitExceededError, match="budget exhausted"):
        await svc._check_budget_preflight(_ctx())


@pytest.mark.asyncio
async def test_budget_preflight_reraises_plan_limit_error_from_controller() -> None:
    """A PlanLimitExceededError raised by the controller itself must propagate
    (fail-closed), not be swallowed by the generic fail-open except clause."""
    svc = _svc()
    state = _AppState()
    state.redis_cost_controller = AsyncMock()
    state.redis_cost_controller.get_budget_status = AsyncMock(
        side_effect=PlanLimitExceededError("hard budget cap hit")
    )
    svc._app_state = state

    with pytest.raises(PlanLimitExceededError, match="hard budget cap hit"):
        await svc._check_budget_preflight(_ctx())


@pytest.mark.asyncio
async def test_budget_preflight_fails_open_on_unexpected_controller_error() -> None:
    """Any other (non-PlanLimitExceededError) failure must fail open — a
    budgeting bug must never block legitimate goal submission."""
    svc = _svc()
    state = _AppState()
    state.redis_cost_controller = AsyncMock()
    state.redis_cost_controller.get_budget_status = AsyncMock(
        side_effect=RuntimeError("redis down")
    )
    svc._app_state = state

    await svc._check_budget_preflight(_ctx())  # must not raise


# ── _check_daily_goal_limit_redis ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_daily_goal_limit_redis_increments_and_sets_ttl_under_limit() -> None:
    svc = _svc()
    redis = AsyncMock()
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    svc._redis = redis

    await svc._check_daily_goal_limit_redis(_ctx())  # must not raise

    redis.incr.assert_awaited_once()
    redis.expire.assert_awaited_once()
    redis.decr.assert_not_called()


@pytest.mark.asyncio
async def test_daily_goal_limit_redis_rolls_back_increment_when_over_limit() -> None:
    """When the atomic INCR pushes the tenant over their daily plan limit,
    the pre-emptive increment must be rolled back (DECR) before the goal is
    rejected — otherwise every rejected submission would still burn a slot."""
    svc = _svc()
    redis = AsyncMock()
    # FREE plan allows 25 goals/day; report count already over that.
    redis.incr = AsyncMock(return_value=26)
    redis.expire = AsyncMock(return_value=True)
    redis.decr = AsyncMock(return_value=25)
    svc._redis = redis

    with pytest.raises(PlanLimitExceededError, match="Daily goal limit"):
        await svc._check_daily_goal_limit_redis(_ctx(plan=PlanTier.FREE))

    redis.decr.assert_awaited_once()


# ── _recover_interrupted_goals ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_recover_interrupted_goals_continues_after_one_enqueue_failure() -> None:
    """If re-enqueuing one interrupted goal raises, the remaining interrupted
    goals must still be recovered rather than aborting the whole sweep."""
    svc = _svc()
    broken = _inject_goal(svc, goal_id="g-broken", status=GoalStatus.EXECUTING)
    broken.task = None
    healthy = _inject_goal(svc, goal_id="g-healthy", status=GoalStatus.PLANNING)
    healthy.task = None

    task_queue = MagicMock()

    def _enqueue(**kwargs: Any) -> None:
        if kwargs["goal_id"] == "g-broken":
            raise RuntimeError("queue backend unavailable")

    task_queue.enqueue_goal = MagicMock(side_effect=_enqueue)
    svc._task_queue = task_queue

    recovered = await svc._recover_interrupted_goals()

    # Only the healthy goal was actually recovered; the broken one keeps its
    # prior (non-terminal) status rather than being silently marked recovered.
    assert recovered == 1
    assert healthy.status == GoalStatus.PLANNING
    assert broken.status == GoalStatus.EXECUTING
    assert task_queue.enqueue_goal.call_count == 2


@pytest.mark.asyncio
async def test_recover_interrupted_goals_marks_failed_without_task_queue() -> None:
    """With no task queue configured at all, interrupted goals can't be
    re-enqueued anywhere — they must be marked FAILED with a clear message
    so the caller knows to resubmit, rather than sitting stuck forever."""
    svc = _svc()
    record = _inject_goal(svc, goal_id="g-orphan", status=GoalStatus.EXECUTING)
    record.task = None
    svc._task_queue = None

    recovered = await svc._recover_interrupted_goals()

    assert recovered == 0
    assert record.status == GoalStatus.FAILED
    assert "restart" in (record.error_message or "").lower()


# ── cancel_goal cross-replica signal ───────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_goal_signals_redis_for_cross_process_workers() -> None:
    """Cancelling a goal that may be executing on a separate Celery worker
    must publish a cancel signal over Redis, not just flip local state."""
    svc = _svc()
    record = _inject_goal(svc, goal_id="g-cancel", status=GoalStatus.EXECUTING)
    redis = AsyncMock()
    redis.set = AsyncMock()
    redis.publish = AsyncMock()
    svc._redis = redis

    result = await svc.cancel_goal("g-cancel", _ctx(tenant_id="cb2-t1"))

    assert result["status"] == "cancelled"
    assert record.status == GoalStatus.CANCELLED
    redis.set.assert_awaited_once()
    # publish may also fire for the goal_cancelled SSE fan-out event; the
    # cross-process cancel signal itself must have published at least once.
    assert redis.publish.await_count >= 1
    assert any(
        call.args and call.args[0] == "goal_cancel:g-cancel"
        for call in redis.publish.await_args_list
    ), "signal_cancel must publish on the goal_cancel:<id> channel"


# ── resume_goal: checkpoint dispatch failure falls back to legacy resume ──


@pytest.mark.asyncio
async def test_resume_goal_falls_back_when_checkpoint_dispatch_raises() -> None:
    """If dispatching the checkpoint 'goal_resumed' event itself raises, the
    outer handler must log and fall through to the legacy asyncio pause-event
    resume path instead of leaving the caller with an unhandled exception."""
    svc = _svc()
    record = GoalRecord(
        goal_id="g-cp",
        goal_text="test",
        status=GoalStatus.WAITING_HUMAN,
        tenant_id="cb2-t1",
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
    )
    svc._goals["g-cp"] = record

    class FakeGraph:
        async def astream(self, input_state: Any, config: Any):
            yield {"result": "ok"}

    class FakeAgentGraph:
        _graph = FakeGraph()

    record._graph_instance = FakeAgentGraph()

    calls = {"n": 0}

    async def _dispatch_side_effect(*args: Any, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("dispatch transport down")
        return None

    svc._dispatch_event = AsyncMock(side_effect=_dispatch_side_effect)  # type: ignore[method-assign]

    result = await svc.resume_goal("g-cp", _ctx(tenant_id="cb2-t1"), approved=True)

    assert result["status"] == "resumed"
    assert record.status == GoalStatus.EXECUTING
    # Fell through to the legacy path, which dispatches goal_resumed again.
    assert calls["n"] == 2


# ── resume_goal: cross-replica Redis pause-flag clearing ───────────────────


@pytest.mark.asyncio
async def test_resume_goal_fallback_path_clears_redis_pause_flag() -> None:
    """The legacy (no stored graph instance) resume path must also clear the
    cross-process Redis pause flag so a Celery worker's is_paused_sync() poll
    stops blocking the goal — not just the local asyncio pause event."""
    svc = _svc()
    record = _inject_goal(svc, goal_id="g-resume-redis", status=GoalStatus.WAITING_HUMAN)
    redis = AsyncMock()
    redis.delete = AsyncMock()
    redis.publish = AsyncMock()
    svc._redis = redis

    result = await svc.resume_goal("g-resume-redis", _ctx(tenant_id="cb2-t1"), approved=True)
    await asyncio.sleep(0.02)  # let the fire-and-forget signal_resume() run

    assert result["status"] == "resumed"
    assert record.status == GoalStatus.EXECUTING
    redis.delete.assert_awaited_once()
    assert redis.publish.await_count >= 1


@pytest.mark.asyncio
async def test_resume_goal_checkpoint_path_clears_redis_pause_flag() -> None:
    """The checkpoint re-invocation resume path must also clear the Redis
    pause flag for cross-process workers, mirroring the legacy path."""
    svc = _svc()
    record = _inject_goal(svc, goal_id="g-resume-redis-cp", status=GoalStatus.WAITING_HUMAN)

    class FakeGraph:
        async def astream(self, input_state: Any, config: Any):
            yield {"result": "ok"}

    class FakeAgentGraph:
        _graph = FakeGraph()

    record._graph_instance = FakeAgentGraph()

    redis = AsyncMock()
    redis.delete = AsyncMock()
    redis.publish = AsyncMock()
    svc._redis = redis

    result = await svc.resume_goal("g-resume-redis-cp", _ctx(tenant_id="cb2-t1"), approved=True)
    await asyncio.sleep(0.02)  # let both fire-and-forget tasks run

    assert result["status"] == "resumed"
    assert record.status == GoalStatus.EXECUTING
    redis.delete.assert_awaited_once()
    assert redis.publish.await_count >= 1


# ── subscribe_events: SSE reconnect / replay / live-delivery scenarios ─────


@pytest.mark.asyncio
async def test_subscribe_events_reconnect_dedupes_and_honours_sentinel() -> None:
    """A client reconnecting with since_sequence must not receive events it
    already saw (queued while it was disconnected), and an end-of-stream
    sentinel encountered while draining the buffered queue must end the
    stream immediately."""
    svc = _svc()
    record = _inject_goal(svc, goal_id="g-sse-1", status=GoalStatus.EXECUTING)

    dup_event = {"type": "step_started", "_seq": 5}

    async def _delayed_since_persisted(*_a: Any, **_kw: Any) -> list[dict[str, Any]]:
        # A real suspension point (unlike a plain AsyncMock) so the test can
        # populate the subscriber queue *before* the generator reaches its
        # buffered-queue drain loop — exactly like a real reconnect where the
        # queue already has events buffered by the time replay finishes.
        await asyncio.sleep(0)
        return [dup_event]

    svc._list_events_since_persisted = _delayed_since_persisted  # type: ignore[method-assign]

    received: list[dict[str, Any] | None] = []

    async def _consume() -> None:
        async for ev in svc.subscribe_events("g-sse-1", _ctx(tenant_id="cb2-t1"), since_sequence=5):
            received.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0)  # let the generator create the queue and hit the suspension above
    assert record.subscribers, "subscribe_events must register a live queue"
    queue = record.subscribers[0]
    # The exact same event already delivered by replay must be suppressed,
    # and the sentinel must end the stream right there (no live wait).
    queue.put_nowait(dict(dup_event))
    queue.put_nowait(None)

    await asyncio.wait_for(task, timeout=1.0)

    assert received == [dup_event], "duplicate replayed event must be suppressed"


@pytest.mark.asyncio
async def test_subscribe_events_delivers_live_event_after_buffer_drains() -> None:
    """Once the buffered replay queue is empty, a live event published later
    must still be delivered through the blocking queue.get() path."""
    svc = _svc()
    record = _inject_goal(svc, goal_id="g-sse-2", status=GoalStatus.EXECUTING)
    svc._list_persisted_events = AsyncMock(return_value=[])  # type: ignore[method-assign]

    received: list[dict[str, Any]] = []

    async def _consume() -> None:
        async for ev in svc.subscribe_events("g-sse-2", _ctx(tenant_id="cb2-t1")):
            received.append(ev)

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.02)  # let it drain the (empty) buffer and block on queue.get()
    assert record.subscribers
    queue = record.subscribers[0]

    live_event = {"type": "step_completed", "step": 1}
    queue.put_nowait(live_event)
    await asyncio.sleep(0.02)
    queue.put_nowait(None)

    await asyncio.wait_for(task, timeout=1.0)

    assert received == [live_event]


@pytest.mark.asyncio
async def test_subscribe_events_ends_stream_when_db_refresh_finds_terminal_status() -> None:
    """If a DB refresh (owning replica already finished the goal) discovers
    the goal is now terminal, the stream must end immediately without ever
    opening a live queue wait — avoiding a client hanging on a dead goal."""
    svc = _svc()
    record = _inject_goal(svc, goal_id="g-sse-3", status=GoalStatus.EXECUTING)
    record.task = None
    svc._db = MagicMock()  # non-None so _should_refresh_goal_from_db can return True
    svc._task_queue = MagicMock()  # forces refresh regardless of record.task/status

    completed_record = GoalRecord(
        goal_id="g-sse-3",
        goal_text="do something",
        status=GoalStatus.COMPLETE,
        tenant_id="cb2-t1",
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
    )
    svc._db_get_goal_record = AsyncMock(return_value=completed_record)  # type: ignore[method-assign]

    events = [
        ev
        async for ev in svc.subscribe_events("g-sse-3", _ctx(tenant_id="cb2-t1"))
    ]

    assert events == []
