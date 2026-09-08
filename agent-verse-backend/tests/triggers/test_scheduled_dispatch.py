"""Governance parity for scheduled trigger fires (WT-9).

Scheduled fires must flow through the TriggerDispatcher so the same dedup /
rate-limit / circuit-breaker / condition governance applies as for every other
trigger type — instead of calling ``run_goal.apply_async`` directly.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.scaling.tasks import _dispatch_scheduled_via_dispatcher
from app.triggers.dispatcher import TriggerDispatcher


class _FakeGoalService:
    """Records create_goal calls and returns a fresh goal id each time."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_goal(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(goal_id=f"g-{len(self.calls)}")


class _FakeRedis:
    """In-memory Redis with SET NX semantics used by the dispatcher's dedup."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    async def set(
        self, key: str, value: Any, *, ex: int | None = None, nx: bool = False
    ) -> Any:
        if nx and key in self._store:
            return None
        self._store[key] = value
        return True

    async def incr(self, key: str) -> int:
        self._store[key] = int(self._store.get(key, 0)) + 1
        return int(self._store[key])

    async def expire(self, *_a: Any, **_k: Any) -> None:
        return None


def _sched(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "trigger_type": "cron",
        "cron_expression": "0 * * * *",
        "goal_template": "Do the scheduled thing",
        "tenant_id": "t-1",
        "tenant_plan": "starter",
    }
    base.update(overrides)
    return base


# ── Deduplication: same scheduled_fire_time fires only once ─────────────────────


async def test_scheduled_fire_deduped_on_same_fire_instance() -> None:
    goal_service = _FakeGoalService()
    redis = _FakeRedis()
    dispatcher = TriggerDispatcher(goal_service=goal_service, redis=redis)

    first = await _dispatch_scheduled_via_dispatcher(
        "sched-key-1",
        _sched(),
        fire_instance_id="2026-09-08T09:00:00Z",
        dispatcher=dispatcher,
    )
    second = await _dispatch_scheduled_via_dispatcher(
        "sched-key-1",
        _sched(),
        fire_instance_id="2026-09-08T09:00:00Z",  # identical fire instance
        dispatcher=dispatcher,
    )

    assert first.goal_created is True
    assert second.skip_reason == "dedup"
    # Exactly one goal was created despite two fire attempts.
    assert len(goal_service.calls) == 1


async def test_distinct_fire_instances_each_create_a_goal() -> None:
    goal_service = _FakeGoalService()
    redis = _FakeRedis()
    dispatcher = TriggerDispatcher(goal_service=goal_service, redis=redis)

    await _dispatch_scheduled_via_dispatcher(
        "sched-key-1",
        _sched(),
        fire_instance_id="2026-09-08T09:00:00Z",
        dispatcher=dispatcher,
    )
    await _dispatch_scheduled_via_dispatcher(
        "sched-key-1",
        _sched(),
        fire_instance_id="2026-09-08T10:00:00Z",  # next hour
        dispatcher=dispatcher,
    )

    assert len(goal_service.calls) == 2


# ── Condition gating: a false condition creates no goal ─────────────────────────


async def test_scheduled_condition_false_creates_no_goal(monkeypatch: Any) -> None:
    goal_service = _FakeGoalService()
    dispatcher = TriggerDispatcher(goal_service=goal_service, redis=_FakeRedis())

    # celpy is optional; force the condition to evaluate false deterministically.
    monkeypatch.setattr(dispatcher, "_evaluate_condition", lambda *_a, **_k: False)

    result = await _dispatch_scheduled_via_dispatcher(
        "sched-key-2",
        _sched(condition="payload.env == 'prod'"),
        fire_instance_id="2026-09-08T09:00:00Z",
        dispatcher=dispatcher,
    )

    assert result.skip_reason == "condition_false"
    assert goal_service.calls == []


# ── Spec construction: schedule fields map onto the TriggerSpec ──────────────────


async def test_scheduled_spec_carries_goal_and_agent() -> None:
    goal_service = _FakeGoalService()
    dispatcher = TriggerDispatcher(goal_service=goal_service, redis=_FakeRedis())

    await _dispatch_scheduled_via_dispatcher(
        "sched-key-3",
        _sched(agent_id="agent-42", goal_template="Rotate the keys"),
        fire_instance_id="2026-09-08T09:00:00Z",
        dispatcher=dispatcher,
    )

    assert len(goal_service.calls) == 1
    call = goal_service.calls[0]
    assert call["goal_text"] == "Rotate the keys"
    assert call["agent_id"] == "agent-42"
    # tenant context flowed through with the schedule's plan.
    assert call["tenant_ctx"].tenant_id == "t-1"
    assert call["tenant_ctx"].plan == "starter"


async def test_missing_goal_and_tenant_returns_none() -> None:
    dispatcher = TriggerDispatcher(goal_service=_FakeGoalService(), redis=_FakeRedis())
    result = await _dispatch_scheduled_via_dispatcher(
        "sched-key-4",
        {"trigger_type": "cron"},  # no goal_template, no tenant_id
        fire_instance_id="2026-09-08T09:00:00Z",
        dispatcher=dispatcher,
    )
    assert result is None
