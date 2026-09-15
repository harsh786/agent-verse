"""Phase 2 — SCHEDULE-intent chat turns create REAL triggers (not just a preview)."""

from __future__ import annotations

from typing import Any

from app.chat.service import ChatService


class _FakeSpec:
    trigger_type = "cron"


class _FakeScheduler:
    def __init__(self) -> None:
        self.parsed: list[str] = []

    async def parse(self, command: str) -> list[Any]:
        self.parsed.append(command)
        return [_FakeSpec(), _FakeSpec()]  # two schedules


class _FakeStore:
    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []

    async def create_async(self, **kwargs: Any) -> str:
        self.created.append(kwargs)
        return f"sched-{len(self.created)}"


def _ctx() -> Any:
    from app.tenancy.context import PlanTier, TenantContext

    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k")


async def test_create_schedule_parses_and_persists_specs() -> None:
    sched, store = _FakeScheduler(), _FakeStore()
    svc = ChatService(nl_scheduler=sched, schedule_store=store)
    assert svc.can_schedule is True

    ids = await svc.create_schedule(
        tenant_ctx=_ctx(), message="every monday at 9am email me the report", agent_id="a1"
    )
    assert ids == ["sched-1", "sched-2"]
    assert sched.parsed == ["every monday at 9am email me the report"]
    # each schedule bound to the NL command + agent
    assert all(c["agent_id"] == "a1" for c in store.created)
    assert all(c["goal_template"] == "every monday at 9am email me the report" for c in store.created)


async def test_create_schedule_without_deps_is_explicit_error() -> None:
    import pytest

    svc = ChatService()
    assert svc.can_schedule is False
    with pytest.raises(RuntimeError, match="scheduling"):
        await svc.create_schedule(tenant_ctx=_ctx(), message="every day at 9")


def test_schedule_parses_dot_minutes_and_specific_date() -> None:
    from app.chat.intent import IntentRouter

    r = IntentRouter()
    c = r.generate_schedule_confirmation("remind me at 6.11pm on 15 sept to call")
    assert c.cron_expression == "11 18 15 9 *"
    assert c.human_schedule == "on Sep 15 at 18:11"


def test_schedule_parses_pm_minutes_and_month_first_date() -> None:
    from app.chat.intent import IntentRouter

    r = IntentRouter()
    c = r.generate_schedule_confirmation("send a report at 6:11 pm on September 20th")
    assert c.cron_expression == "11 18 20 9 *"
    assert c.human_schedule == "on Sep 20 at 18:11"


def test_schedule_day_of_week_keeps_minutes() -> None:
    from app.chat.intent import IntentRouter

    r = IntentRouter()
    c = r.generate_schedule_confirmation("every Tuesday at 14:30 summarize")
    assert c.cron_expression == "30 14 * * 2"
    assert c.human_schedule == "every Tuesday at 14:30"
