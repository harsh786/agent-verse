"""TRG-33: api_poll honours poll_interval_seconds instead of polling every beat tick.

poll_interval_seconds was required on create but never read, so every api_poll
trigger polled its URL on the 60s beat cadence.
"""

from __future__ import annotations

import json
from typing import Any

import fakeredis
import pytest

from app.scaling import tasks
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.validation import validate_spec


def _run_ticks(monkeypatch: pytest.MonkeyPatch, sched: dict[str, Any], ticks: int) -> int:
    r = fakeredis.FakeRedis(decode_responses=True)
    r.set("schedule:t1:poll", json.dumps(sched))
    polls: list[str] = []

    def fake_fetch(url: str, **_k: Any) -> Any:
        polls.append(url)
        return {"status": "ok"}

    monkeypatch.setenv("REDIS_URL", "redis://fake")
    monkeypatch.setenv("AGENTVERSE_DB_SCHEDULE_DISCOVERY", "false")
    monkeypatch.setattr("redis.from_url", lambda *_a, **_k: r)
    monkeypatch.setattr("app.triggers.polling.fetch_json", fake_fetch)
    monkeypatch.setattr(tasks.run_scheduled_goal, "apply_async", lambda **_k: None)
    for _ in range(ticks):
        r.delete("beat_guard:fire_due_schedules")  # each call is a new beat tick
        tasks.fire_due_schedules()
    return len(polls)


def _sched(interval: int) -> dict[str, Any]:
    return {
        "schedule_id": "poll",
        "tenant_id": "t1",
        "trigger_type": "api_poll",
        "goal_template": "Check",
        "poll_url": "https://status.example.com/api",
        "poll_jsonpath": "status",
        "poll_interval_seconds": interval,
        "paused": False,
    }


def test_a_300s_poll_trigger_polls_once_across_five_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _run_ticks(monkeypatch, _sched(300), ticks=5) == 1


def test_a_short_interval_still_polls_at_the_beat_cadence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _run_ticks(monkeypatch, _sched(10), ticks=1) == 1


def test_poll_interval_respects_the_plan_floor() -> None:
    spec = TriggerSpec(
        trigger_type=TriggerType.API_POLL, poll_url="https://x.example", poll_interval_seconds=60
    )
    with pytest.raises(ValueError, match="free plan allows"):
        validate_spec(spec, plan="free")
    validate_spec(spec, plan="enterprise")
