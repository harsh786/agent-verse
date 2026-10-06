"""B7-1: platform-event triggers carry lineage and never loop on their own output.

A goal started by a goal_completed / goal_failed / goal_score_below /
hitl_approved / hitl_rejected / memory_created trigger carries its chain depth
and the id of the trigger that started it. A trigger never fires on an event its
own goal produced (unless ``allow_self_trigger`` is set), and no chain goes past
MAX_CHAIN_DEPTH. Every suppressed firing is audited in ``trigger_events``.

The memory and HITL consumers used to dispatch with no lineage at all: a
memory_created trigger's goal writes a learning when it completes, which is a new
``memory.created`` event, which fired the trigger again — with no depth cap.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.tenancy.context import PlanTier, TenantContext
from app.triggers.consumers.chain import ChainTriggerConsumer, build_chain_event
from app.triggers.consumers.hitl import HITLTriggerConsumer
from app.triggers.consumers.memory import MemoryTriggerConsumer
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.events import TriggerEvent
from app.triggers.lineage import MAX_CHAIN_DEPTH, GoalLineage
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore

CTX = TenantContext(tenant_id="t-b7", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _Redis:
    """SET NX dedup + the counters the rate limiter / bulkhead use."""

    def __init__(self) -> None:
        self.kv: dict[str, Any] = {}

    async def set(self, key: str, value: Any, ex: int | None = None, nx: bool = False) -> Any:
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def incr(self, key: str) -> int:
        self.kv[key] = int(self.kv.get(key, 0)) + 1
        return int(self.kv[key])

    async def decr(self, key: str) -> int:
        self.kv[key] = int(self.kv.get(key, 0)) - 1
        return int(self.kv[key])

    async def expire(self, *_: Any, **__: Any) -> bool:
        return True

    async def delete(self, *keys: str) -> int:
        return sum(1 for k in keys if self.kv.pop(k, None) is not None)


class _GoalService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_goal(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"goal_id": f"created-{len(self.calls)}"}


class _Dispatcher(TriggerDispatcher):
    """Real dispatcher; goal lineage from a dict instead of the goals table, and
    every persisted trigger_events row (fires AND skips) captured."""

    def __init__(self, lineage: dict[str, GoalLineage] | None = None, **kw: Any) -> None:
        self.goals = _GoalService()
        super().__init__(goal_service=self.goals, redis=_Redis(), **kw)
        self.lineage = lineage or {}
        self.audit: list[TriggerEvent] = []
        self.lineage_reads: list[tuple[str, str]] = []

    async def resolve_goal_lineage(self, tenant_id: str, goal_id: str) -> GoalLineage | None:
        self.lineage_reads.append((tenant_id, goal_id))
        return self.lineage.get(goal_id)

    async def resolve_tenant_plan(self, tenant_id: str) -> Any:
        return "professional"

    async def _persist_event(self, event: TriggerEvent) -> None:
        self.audit.append(event)


def _store_with(spec: TriggerSpec) -> tuple[ScheduleStore, str]:
    store = ScheduleStore()
    trigger_id = store.create(spec=spec, tenant_ctx=CTX, goal_id="", goal_template="react")
    return store, trigger_id


def _memory_msg(memory_id: str, source_goal_id: str, **extra: Any) -> dict[str, Any]:
    data = {
        "tenant_id": CTX.tenant_id,
        "memory_id": memory_id,
        "memory_type": "success_pattern",
        "source_goal_id": source_goal_id,
        **extra,
    }
    return {"type": "message", "channel": "memory.created", "data": json.dumps(data)}


def _hitl_msg(channel: str, request_id: str, goal_id: str, **extra: Any) -> dict[str, Any]:
    data = {
        "tenant_id": CTX.tenant_id,
        "request_id": request_id,
        "goal_id": goal_id,
        "hitl_queue_ids": [],
        "hitl_queue_id": "",
        **extra,
    }
    return {"type": "message", "channel": channel, "data": json.dumps(data)}


# ── memory_created ────────────────────────────────────────────────────────────


async def test_memory_trigger_does_not_refire_on_the_memory_its_own_goal_wrote() -> None:
    store, trigger_id = _store_with(TriggerSpec(trigger_type=TriggerType.MEMORY_CREATED))
    disp = _Dispatcher()
    consumer = MemoryTriggerConsumer(trigger_store=store, dispatcher=disp)

    # A memory written by an ordinary goal fires the trigger once.
    await consumer._handle(_memory_msg("m-1", "goal-user"))
    assert len(disp.goals.calls) == 1
    first = disp.goals.calls[0]
    assert first["trigger_chain_depth"] == 1
    assert first["source_trigger_id"] == trigger_id

    # That goal completes and writes its learning: the trigger must NOT re-fire.
    disp.lineage["created-1"] = GoalLineage(depth=1, source_trigger_id=trigger_id)
    await consumer._handle(_memory_msg("m-2", "created-1"))
    assert len(disp.goals.calls) == 1
    assert disp.lineage_reads[-1] == (CTX.tenant_id, "created-1")
    skips = [e for e in disp.audit if e.skip_reason == "self_trigger"]
    assert len(skips) == 1 and skips[0].trigger_id == trigger_id


async def test_memory_trigger_allowed_to_self_trigger_still_stops_at_the_depth_cap() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.MEMORY_CREATED, allow_self_trigger=True)
    store, trigger_id = _store_with(spec)
    disp = _Dispatcher()
    consumer = MemoryTriggerConsumer(trigger_store=store, dispatcher=disp)

    disp.lineage["g-mid"] = GoalLineage(depth=3, source_trigger_id=trigger_id)
    await consumer._handle(_memory_msg("m-a", "g-mid"))
    assert [c["trigger_chain_depth"] for c in disp.goals.calls] == [4]

    disp.lineage["g-deep"] = GoalLineage(depth=MAX_CHAIN_DEPTH, source_trigger_id=trigger_id)
    await consumer._handle(_memory_msg("m-b", "g-deep"))
    assert len(disp.goals.calls) == 1


async def test_one_memory_event_delivered_twice_fires_once() -> None:
    store, _ = _store_with(TriggerSpec(trigger_type=TriggerType.MEMORY_CREATED))
    disp = _Dispatcher()
    replica_a = MemoryTriggerConsumer(trigger_store=store, dispatcher=disp)
    replica_b = MemoryTriggerConsumer(trigger_store=store, dispatcher=disp)
    await replica_a._handle(_memory_msg("m-dup", "goal-user"))
    # Same memory, republished (an idempotent re-store) with a different plan stamp.
    await replica_b._handle(_memory_msg("m-dup", "goal-user", tenant_plan="enterprise"))
    assert len(disp.goals.calls) == 1


async def test_memory_lineage_read_failure_fails_closed() -> None:
    store, _ = _store_with(TriggerSpec(trigger_type=TriggerType.MEMORY_CREATED))

    class _Down(_Dispatcher):
        async def resolve_goal_lineage(self, tenant_id: str, goal_id: str) -> GoalLineage | None:
            raise RuntimeError("postgres down")

    disp = _Down()
    consumer = MemoryTriggerConsumer(trigger_store=store, dispatcher=disp)
    with pytest.raises(RuntimeError):
        await consumer._handle(_memory_msg("m-x", "goal-user"))
    assert disp.goals.calls == []


# ── hitl_approved / hitl_rejected ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("ttype", "channel"),
    [(TriggerType.HITL_APPROVED, "hitl.approved"), (TriggerType.HITL_REJECTED, "hitl.rejected")],
)
async def test_hitl_trigger_does_not_refire_on_its_own_goals_approval(
    ttype: TriggerType, channel: str
) -> None:
    store, trigger_id = _store_with(TriggerSpec(trigger_type=ttype))
    disp = _Dispatcher()
    consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=disp)

    await consumer._handle(_hitl_msg(channel, "req-1", "goal-user"))
    assert len(disp.goals.calls) == 1
    assert disp.goals.calls[0]["trigger_chain_depth"] == 1
    assert disp.goals.calls[0]["source_trigger_id"] == trigger_id

    disp.lineage["created-1"] = GoalLineage(depth=1, source_trigger_id=trigger_id)
    await consumer._handle(_hitl_msg(channel, "req-2", "created-1"))
    assert len(disp.goals.calls) == 1
    assert any(e.skip_reason == "self_trigger" for e in disp.audit)


async def test_one_hitl_decision_published_twice_fires_once() -> None:
    store, _ = _store_with(TriggerSpec(trigger_type=TriggerType.HITL_APPROVED))
    disp = _Dispatcher()
    consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=disp)
    await consumer._handle(_hitl_msg("hitl.approved", "req-9", "goal-user", approver="a"))
    # The same decision relayed again (another replica's view: different note).
    await consumer._handle(
        _hitl_msg("hitl.approved", "req-9", "goal-user", approver="a", note="again")
    )
    assert len(disp.goals.calls) == 1


async def test_hitl_trigger_at_the_depth_cap_does_not_fire() -> None:
    store, _ = _store_with(TriggerSpec(trigger_type=TriggerType.HITL_REJECTED))
    disp = _Dispatcher({"g-deep": GoalLineage(depth=MAX_CHAIN_DEPTH, source_trigger_id="other")})
    consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=disp)
    await consumer._handle(_hitl_msg("hitl.rejected", "req-d", "g-deep"))
    assert disp.goals.calls == []


# ── goal_completed / goal_failed / goal_score_below ───────────────────────────


@pytest.mark.parametrize(
    ("ttype", "channel"),
    [
        (TriggerType.GOAL_COMPLETED, "goal.completed"),
        (TriggerType.GOAL_FAILED, "goal.failed"),
        (TriggerType.GOAL_SCORE_BELOW, "goal.score_below"),
    ],
)
async def test_goal_event_self_trigger_is_audited_and_opt_in(
    ttype: TriggerType, channel: str
) -> None:
    spec = TriggerSpec(trigger_type=ttype, score_threshold=0.9)
    store, trigger_id = _store_with(spec)
    disp = _Dispatcher()
    consumer = ChainTriggerConsumer(trigger_store=store, dispatcher=disp)
    own = build_chain_event(
        channel=channel, tenant_id=CTX.tenant_id, goal_id="created-1",
        trigger_chain_depth=1, source_trigger_id=trigger_id, score=0.1,
    )
    await consumer._handle({"channel": channel, "data": own})
    assert disp.goals.calls == []
    assert [e.skip_reason for e in disp.audit] == ["self_trigger"]

    # Explicitly allowed: it fires on its own goal, one level deeper.
    spec.allow_self_trigger = True
    await consumer._handle({"channel": channel, "data": own})
    assert [c["trigger_chain_depth"] for c in disp.goals.calls] == [2]


async def test_dispatcher_refuses_a_chained_fire_past_the_depth_cap() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED)
    spec.trigger_id = "tr-cap"  # type: ignore[attr-defined]
    disp = _Dispatcher()
    event = await disp.dispatch(
        spec, {"goal_id": "g", "trigger_chain_depth": MAX_CHAIN_DEPTH + 1}, CTX,
        source_goal_id="g", completion_event_id="g:goal.completed",
    )
    assert disp.goals.calls == []
    assert getattr(event, "skip_reason", None) == "chain_depth_exceeded"
    assert [e.skip_reason for e in disp.audit] == ["chain_depth_exceeded"]


@pytest.mark.parametrize(
    "ttype",
    [TriggerType.HITL_APPROVED, TriggerType.HITL_REJECTED, TriggerType.MEMORY_CREATED],
)
async def test_dispatcher_stamps_lineage_on_every_platform_event_goal(ttype: TriggerType) -> None:
    spec = TriggerSpec(trigger_type=ttype)
    spec.trigger_id = "tr-x"  # type: ignore[attr-defined]
    disp = _Dispatcher()
    await disp.dispatch(spec, {"trigger_chain_depth": 3}, CTX, completion_event_id="e-1")
    assert disp.goals.calls[0]["source_trigger_id"] == "tr-x"
    assert disp.goals.calls[0]["trigger_chain_depth"] == 3


def test_allow_self_trigger_is_persisted_only_when_set() -> None:
    from app.triggers.store import spec_config

    assert "allow_self_trigger" not in spec_config(TriggerSpec(TriggerType.MEMORY_CREATED))
    on = TriggerSpec(trigger_type=TriggerType.MEMORY_CREATED, allow_self_trigger=True)
    assert spec_config(on)["allow_self_trigger"] is True


# ── the lineage the memory publisher needs: the source goal id ────────────────


async def test_goal_learning_memory_records_its_source_goal() -> None:
    from app.memory.long_term import LongTermMemoryStore

    store = LongTermMemoryStore()
    mem = await store.extract_from_goal_async(
        goal="g", result="r", tenant_ctx=CTX, goal_id="goal-77"
    )
    assert mem.source_goal_id == "goal-77"


async def test_resolve_goal_lineage_without_a_db_is_unknown() -> None:
    assert await TriggerDispatcher().resolve_goal_lineage("t", "g") is None
