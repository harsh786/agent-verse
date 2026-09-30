"""TRG-04: a goal_completed / goal_failed trigger keeps the agent it RUNS separate
from the agent whose goals it WATCHES, and never re-fires on a goal it created.

The record-level ``agent_id`` ("run agent X") used to be copied onto the spec's
``watch_agent_id`` — the chain consumer's SOURCE filter — so "when any goal
completes, run agent X" fired only on X's goals and then re-fired on the goal it
had just created, until MAX_CHAIN_DEPTH.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.state import GoalStatus
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext
from app.triggers.consumers.chain import ChainTriggerConsumer, build_chain_event
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore, bind_refs_to_spec, spec_config

CTX = TenantContext(tenant_id="t-chain", plan=PlanTier.PROFESSIONAL, api_key_id="k")


class _GoalService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_goal(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"goal_id": f"created-{len(self.calls)}"}


def _event(goal_id: str, agent_id: str, **extra: Any) -> dict[str, Any]:
    raw = build_chain_event(
        channel="goal.completed", tenant_id=CTX.tenant_id, goal_id=goal_id,
        agent_id=agent_id, status="complete", tenant_plan="professional", **extra,
    )
    return {"type": "message", "channel": "goal.completed", "data": raw}


def _wire(**create: Any) -> tuple[str, ChainTriggerConsumer, _GoalService]:
    store = ScheduleStore()
    trigger_id = store.create(
        spec=create.pop("spec", TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED)),
        tenant_ctx=CTX, goal_id="", **create,
    )
    gs = _GoalService()
    consumer = ChainTriggerConsumer(
        trigger_store=store, dispatcher=TriggerDispatcher(goal_service=gs)
    )
    return trigger_id, consumer, gs


def test_bind_refs_keeps_run_agent_off_the_watch_filter() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED)
    bind_refs_to_spec(spec, agent_id="agent-x", goal_template="")
    assert spec.watch_agent_id == ""
    assert spec.agent_id == "agent-x"  # type: ignore[attr-defined]


async def test_run_agent_x_fires_on_other_agents_goal_and_not_on_its_own_goal() -> None:
    trigger_id, consumer, gs = _wire(agent_id="agent-x", goal_template="follow up")

    await consumer._handle(_event("goal-of-y", "agent-y"))
    assert len(gs.calls) == 1
    assert gs.calls[0]["agent_id"] == "agent-x"
    assert gs.calls[0]["source_trigger_id"] == trigger_id

    # The goal the trigger just created completes: it must not re-fire the trigger.
    await consumer._handle(
        _event("created-1", "agent-x", trigger_chain_depth=1, source_trigger_id=trigger_id)
    )
    assert len(gs.calls) == 1


async def test_self_chain_guard_does_not_block_other_triggers() -> None:
    _, consumer, gs = _wire(agent_id="agent-x", goal_template="follow up")
    await consumer._handle(
        _event("created-by-other", "agent-q", trigger_chain_depth=1, source_trigger_id="other")
    )
    assert len(gs.calls) == 1


async def test_explicit_watch_filter_and_run_agent_are_independent() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED, watch_agent_id="agent-y")
    _, consumer, gs = _wire(spec=spec, agent_id="agent-x", goal_template="follow up")

    await consumer._handle(_event("goal-of-z", "agent-z"))
    assert gs.calls == []
    await consumer._handle(_event("goal-of-y", "agent-y"))
    assert [c["agent_id"] for c in gs.calls] == ["agent-x"]


async def test_watch_filter_alone_is_not_the_run_target() -> None:
    gs = _GoalService()
    spec = TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED, watch_agent_id="agent-y",
                       goal_template="summarise")
    spec.trigger_id = "tr-1"  # type: ignore[attr-defined]
    await TriggerDispatcher(goal_service=gs).dispatch(spec, {"goal_id": "g"}, CTX)
    assert gs.calls[0]["agent_id"] is None


def test_explicit_watch_agent_is_persisted_and_run_agent_is_not() -> None:
    spec = TriggerSpec(trigger_type=TriggerType.GOAL_COMPLETED, watch_agent_id="agent-y")
    bind_refs_to_spec(spec, agent_id="agent-x")
    cfg = spec_config(spec)
    assert cfg.get("watch_agent_id") == "agent-y"
    assert "agent_id" not in cfg


# ── the originating trigger id travels with the goal to its lifecycle event ──


def _record(goal_id: str, **ctx: Any) -> GoalRecord:
    return GoalRecord(
        goal_id=goal_id, goal_text="g", status=GoalStatus.EXECUTING,
        tenant_id=CTX.tenant_id, priority="normal", dry_run=False,
        created_at=datetime.now(UTC).isoformat(), agent_id="agent-x",
        execution_context=dict(ctx),
    )


async def test_goal_service_records_and_republishes_source_trigger_id() -> None:
    svc = GoalService()
    svc.submit_goal = AsyncMock(return_value={"goal_id": "new"})  # type: ignore[method-assign]
    await svc.create_goal(tenant_ctx=CTX, goal_text="next", source_trigger_id="tr-9")
    assert svc.submit_goal.await_args.kwargs["execution_context"]["source_trigger_id"] == "tr-9"

    svc2 = GoalService()
    redis = AsyncMock()
    svc2._redis = redis
    svc2._goals["g1"] = _record("g1", trigger_chain_depth=1, source_trigger_id="tr-9")
    await svc2._dispatch_event("g1", {"type": "goal_complete"}, tenant_ctx=CTX)
    (raw,) = [c.args[1] for c in redis.publish.call_args_list if c.args[0] == "goal.completed"]
    assert json.loads(raw)["source_trigger_id"] == "tr-9"


def test_celery_queue_forwards_source_trigger_id_only_when_set() -> None:
    from app.services.goal_queue import CeleryGoalTaskQueue

    base = dict(goal_id="g", tenant_id="t", goal_text="x", priority="normal", dry_run=False)
    with patch("app.scaling.tasks.run_goal.apply_async") as aa:
        CeleryGoalTaskQueue().enqueue_goal(**base, source_trigger_id="tr-9")
        assert aa.call_args.kwargs["kwargs"]["source_trigger_id"] == "tr-9"
        CeleryGoalTaskQueue().enqueue_goal(**base)
        assert "source_trigger_id" not in aa.call_args.kwargs["kwargs"]


def test_run_goal_accepts_source_trigger_id() -> None:
    import inspect

    from app.scaling.tasks import run_goal

    assert inspect.signature(run_goal.run).parameters["source_trigger_id"].default == ""


async def test_dispatcher_sends_source_trigger_id_only_for_goal_event_triggers() -> None:
    gs = SimpleNamespace(create_goal=AsyncMock(return_value={"goal_id": "x"}))
    d = TriggerDispatcher(goal_service=gs)
    cron = TriggerSpec(trigger_type=TriggerType.CRON)
    cron.trigger_id = "tr-cron"  # type: ignore[attr-defined]
    await d._create_goal(cron, "t", CTX, "idem")
    assert "source_trigger_id" not in gs.create_goal.await_args.kwargs


@pytest.mark.parametrize(
    "ttype", [TriggerType.GOAL_COMPLETED, TriggerType.GOAL_FAILED, TriggerType.GOAL_SCORE_BELOW]
)
async def test_dispatcher_stamps_goal_event_triggers(ttype: TriggerType) -> None:
    gs = SimpleNamespace(create_goal=AsyncMock(return_value={"goal_id": "x"}))
    spec = TriggerSpec(trigger_type=ttype)
    spec.trigger_id = "tr-chain"  # type: ignore[attr-defined]
    await TriggerDispatcher(goal_service=gs)._create_goal(spec, "t", CTX, "idem")
    assert gs.create_goal.await_args.kwargs["source_trigger_id"] == "tr-chain"
