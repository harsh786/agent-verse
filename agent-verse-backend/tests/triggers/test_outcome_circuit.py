"""TRG-13: a trigger whose goals keep failing stops firing — on every process.

The breaker was an in-process dict fed only by goal *enqueue* failures, and each
``run_scheduled_goal`` built a fresh dispatcher (fresh registry), so a schedule
whose goals failed every time kept dispatching every tick. The circuit is now
read from the durable record of the trigger's goals (trigger_events → goals),
so it is shared by every worker/replica and fed by real goal outcomes.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any

from app.tenancy.context import PlanTier
from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.outcome_circuit import (
    COOLDOWN_SECONDS,
    FAILURE_THRESHOLD,
    evaluate_outcomes,
)

NOW = dt.datetime(2026, 9, 30, 12, 0, 0, tzinfo=dt.UTC)


def _rows(*statuses: str, newest_age_s: int = 60) -> list[tuple[str, dt.datetime]]:
    """Newest first, one minute apart, the newest ``newest_age_s`` seconds old."""
    return [(s, NOW - dt.timedelta(seconds=newest_age_s + 60 * i)) for i, s in enumerate(statuses)]


def test_closed_until_the_threshold_of_consecutive_failures() -> None:
    assert evaluate_outcomes(_rows(*["failed"] * (FAILURE_THRESHOLD - 1)), NOW).state == "closed"
    assert evaluate_outcomes(_rows("complete", *["failed"] * 9), NOW).state == "closed"


def test_opens_after_consecutive_failed_goals_then_probes_after_cooldown() -> None:
    failing = ["failed"] * FAILURE_THRESHOLD
    opened = evaluate_outcomes(_rows(*failing), NOW)
    assert opened.state == "open"
    assert opened.retry_at == NOW - dt.timedelta(seconds=60) + dt.timedelta(
        seconds=COOLDOWN_SECONDS
    )
    cooled = evaluate_outcomes(_rows(*failing, newest_age_s=COOLDOWN_SECONDS + 1), NOW)
    assert cooled.state == "half_open"  # this fire is the probe


def test_one_probe_at_a_time() -> None:
    probing = _rows("executing", *["failed"] * FAILURE_THRESHOLD, newest_age_s=5)
    assert evaluate_outcomes(probing, NOW).state == "open"


class _Session:
    def __init__(self, rows: list[tuple[str, dt.datetime]]) -> None:
        self._rows = rows

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        sql = str(stmt)
        if "JOIN goals" in sql:
            return SimpleNamespace(fetchall=lambda: list(self._rows))
        return SimpleNamespace(fetchall=list, scalar=lambda: None, first=lambda: None)


class _Goals:
    def __init__(self) -> None:
        self.created: list[Any] = []

    async def create_goal(self, **kw: Any) -> Any:
        self.created.append(kw)
        return SimpleNamespace(goal_id="g")


def _spec() -> TriggerSpec:
    spec = TriggerSpec(trigger_type=TriggerType.CRON, cron_expression="* * * * *")
    spec.trigger_id = "sched-1"  # type: ignore[attr-defined]
    return spec


async def test_every_process_skips_a_trigger_whose_goals_keep_failing() -> None:
    """Two dispatchers (two worker processes) share only the database."""
    failing = [
        ("failed", dt.datetime.now(dt.UTC) - dt.timedelta(minutes=i + 1))
        for i in range(FAILURE_THRESHOLD)
    ]
    ctx = SimpleNamespace(tenant_id="t1", plan=PlanTier.ENTERPRISE)
    for _process in range(2):
        goals = _Goals()
        dispatcher = TriggerDispatcher(
            goal_service=goals, db_session_factory=lambda: _Session(failing)
        )
        event = await dispatcher.dispatch(
            _spec(), {}, ctx, scheduled_fire_time=f"2026-09-30T12:0{_process}:00"
        )
        assert event.skip_reason == "circuit_open"
        assert goals.created == []


async def test_circuit_endpoint_reports_open_state_and_retry_time() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.triggers.store import ScheduleStore
    from tests.api.test_schedules_api import _CTX, _VALID_KEY, _make_app

    store = ScheduleStore()
    sid = await store.create_async(
        goal_id="g",
        spec=TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=3600),
        tenant_ctx=_CTX,
    )
    newest = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    failing = [("failed", newest - dt.timedelta(minutes=i)) for i in range(FAILURE_THRESHOLD)]
    app = _make_app(schedule_store=store)
    app.state.db_session_factory = lambda: _Session(failing)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        r = await client.get(f"/schedules/{sid}/circuit", headers={"X-API-Key": _VALID_KEY})
        missing = await client.get("/schedules/nope/circuit", headers={"X-API-Key": _VALID_KEY})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "open"
    assert body["consecutive_failures"] == FAILURE_THRESHOLD
    assert dt.datetime.fromisoformat(body["retry_at"]) == newest + dt.timedelta(
        seconds=COOLDOWN_SECONDS
    )
    assert missing.status_code == 404


async def test_healthy_trigger_still_fires() -> None:
    ok = [("complete", dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1))]
    goals = _Goals()
    dispatcher = TriggerDispatcher(goal_service=goals, db_session_factory=lambda: _Session(ok))
    event = await dispatcher.dispatch(
        _spec(),
        {},
        SimpleNamespace(tenant_id="t1", plan=PlanTier.ENTERPRISE),
        scheduled_fire_time="2026-09-30T12:00:00",
    )
    assert event.goal_created is True
