"""TRG-07: a CEL condition set via ``condition_cel`` gates beat-fired triggers.

The API stores ``condition_cel`` as ``TriggerSpec.condition_expression`` (it
reaches the beat's schedule dict through the ``config`` JSONB), but the beat
forwarded only ``sched["condition"]`` — so a cron / interval / rss / api_poll /
file_drop trigger with a CEL condition fired unconditionally (fail open).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.scaling import tasks
from app.triggers.dispatcher import TriggerDispatcher


class _FakeGoalService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_goal(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(goal_id=f"g-{len(self.calls)}")


def _sched(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schedule_id": "s-1",
        "trigger_type": "cron",
        "cron_expression": "* * * * *",
        "goal_template": "Do the scheduled thing",
        "tenant_id": "t-1",
        "condition": "",
        "condition_expression": "false",
    }
    base.update(overrides)
    return base


def test_stored_schedule_dict_carries_condition_expression() -> None:
    """The beat's schedule dict (DB config JSONB / Redis payload) has the field."""
    from app.triggers.models import TriggerSpec, TriggerType
    from app.triggers.store import spec_config

    spec = TriggerSpec(
        trigger_type=TriggerType.CRON, cron_expression="* * * * *", condition_expression="false"
    )
    assert spec_config(spec)["condition_expression"] == "false"


def test_spec_carries_condition_expression() -> None:
    spec = tasks._build_scheduled_trigger_spec("schedule:t-1:s-1", _sched())
    assert spec.condition_expression == "false"


async def test_false_condition_expression_blocks_a_due_fire() -> None:
    goal_service = _FakeGoalService()
    dispatcher = TriggerDispatcher(goal_service=goal_service, redis=None)

    event = await tasks._dispatch_scheduled_via_dispatcher(
        "schedule:t-1:s-1",
        _sched(),
        fire_instance_id="2026-09-30T09:00:00Z",
        dispatcher=dispatcher,
    )

    assert event.skip_reason == "condition_false"
    assert goal_service.calls == []


def test_beat_enqueue_forwards_condition_expression(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        tasks.run_scheduled_goal,
        "apply_async",
        lambda *, kwargs, queue: sent.append(kwargs),
    )

    tasks._enqueue_governed_fire(
        "schedule:t-1:s-1",
        _sched(),
        goal_template="Do the scheduled thing",
        tenant_id="t-1",
        agent_id="",
        fire_instance_id="2026-09-30T09:00:00Z",
    )

    assert sent[0]["condition_expression"] == "false"


def test_task_passes_condition_expression_to_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    async def fake_dispatch(_key: str, sched: dict[str, Any], **_kw: Any) -> Any:
        seen.append(sched)
        return SimpleNamespace(goal_created=False, goal_id=None, skip_reason="condition_false")

    monkeypatch.setattr(tasks, "_dispatch_scheduled_via_dispatcher", fake_dispatch)
    monkeypatch.setattr(tasks, "_build_worker_goal_service", lambda: (None, None))
    monkeypatch.setattr(tasks, "_worker_async_redis", lambda: None)

    result = tasks.run_scheduled_goal.run(
        "schedule:t-1:s-1",
        "t-1",
        "Do the scheduled thing",
        condition_expression="false",
    )

    assert result["status"] == "skipped"
    assert seen[0]["condition_expression"] == "false"
