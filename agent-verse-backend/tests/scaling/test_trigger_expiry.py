"""TRG-09: ``expires_at_iso`` is enforced — an expired trigger stops firing.

It was validated on create but no firing path read it, so an expired trigger
kept firing forever.
"""

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace
from typing import Any

import fakeredis
import pytest

from app.scaling import tasks
from app.tenancy.context import PlanTier
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.validation import is_trigger_expired

_NOW = datetime.datetime(2026, 9, 30, 12, 0, 0)


@pytest.mark.parametrize(
    ("value", "expired"),
    [
        ("", False),
        ("2026-09-30T11:59:00", True),
        ("2026-09-30T12:00:00Z", True),
        ("2026-09-30T13:59:00+02:00", True),  # 11:59 UTC
        ("2026-09-30T14:30:00+02:00", False),  # 12:30 UTC
        ("2026-09-30T12:01:00", False),
        ("2026-10-01T00:00:00+00:00", False),
        ("not-a-date", True),  # unparseable fails closed
    ],
)
def test_is_trigger_expired(value: str, expired: bool) -> None:
    assert is_trigger_expired(value, now=_NOW) is expired


class _FakeGoalService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create_goal(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(goal_id="g-1")


async def test_dispatcher_skips_an_expired_trigger() -> None:
    goal_service = _FakeGoalService()
    dispatcher = TriggerDispatcher(goal_service=goal_service, redis=None)
    spec = TriggerSpec(
        trigger_type=TriggerType.WEBHOOK,
        goal_template="Handle it",
        expires_at_iso="2020-01-01T00:00:00Z",
    )
    spec.trigger_id = "trg-1"  # type: ignore[attr-defined]

    event = await dispatcher.dispatch(
        spec,
        {"x": 1},
        SimpleNamespace(tenant_id="t-1", plan=PlanTier.FREE),
        message_id="m-1",
    )

    assert event.skip_reason == "expired"
    assert goal_service.calls == []


def test_beat_forwards_expiry_to_the_governed_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        tasks.run_scheduled_goal, "apply_async", lambda *, kwargs, queue: sent.append(kwargs)
    )
    tasks._enqueue_governed_fire(
        "schedule:t-1:s-1",
        {"trigger_type": "cron", "expires_at_iso": "2030-01-01T00:00:00Z"},
        goal_template="Do it",
        tenant_id="t-1",
        agent_id="",
        fire_instance_id="f-1",
    )
    assert sent[0]["expires_at_iso"] == "2030-01-01T00:00:00Z"
    spec = tasks._build_scheduled_trigger_spec(
        "schedule:t-1:s-1", {"expires_at_iso": "2030-01-01T00:00:00Z"}
    )
    assert spec.expires_at_iso == "2030-01-01T00:00:00Z"


def test_beat_does_not_fire_and_auto_pauses_an_expired_cron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = fakeredis.FakeRedis(decode_responses=True)
    key = "schedule:t-1:s-exp"
    r.set(
        key,
        json.dumps(
            {
                "schedule_id": "s-exp",
                "tenant_id": "t-1",
                "goal_template": "Every minute",
                "trigger_type": "cron",
                "cron_expression": "* * * * *",
                "timezone": "UTC",
                "paused": False,
                "expires_at_iso": "2020-01-01T00:00:00Z",
            }
        ),
    )
    live = "schedule:t-1:s-live"
    r.set(
        live,
        json.dumps(
            {
                "schedule_id": "s-live",
                "tenant_id": "t-1",
                "goal_template": "Every minute",
                "trigger_type": "cron",
                "cron_expression": "* * * * *",
                "timezone": "UTC",
                "paused": False,
                "expires_at_iso": "2999-01-01T00:00:00Z",
            }
        ),
    )
    sent: list[dict[str, Any]] = []
    monkeypatch.setenv("REDIS_URL", "redis://fake")
    monkeypatch.delenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", raising=False)
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: r)
    monkeypatch.setattr(
        tasks.run_scheduled_goal, "apply_async", lambda *, kwargs, queue: sent.append(kwargs)
    )

    tasks.fire_due_schedules()

    assert [k["schedule_id"] for k in sent] == [live]
    assert json.loads(r.get(key))["paused"] is True
    assert json.loads(r.get(live))["paused"] is False
