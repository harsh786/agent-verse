"""Phase 2 — SCHEDULE-intent chat turns create REAL triggers (not just a preview)."""

from __future__ import annotations

from typing import Any

import pytest

from app.chat.service import ChatService
from app.triggers.models import TriggerSpec, TriggerType


def _cron(expr: str = "0 9 * * 1") -> TriggerSpec:
    return TriggerSpec(trigger_type=TriggerType.CRON, cron_expression=expr)


class _FakeScheduler:
    def __init__(self, specs: list[TriggerSpec] | None = None) -> None:
        self.parsed: list[str] = []
        self.tenants: list[Any] = []
        self._specs = specs

    async def parse(self, command: str, *, tenant_ctx: Any = None) -> list[Any]:
        self.parsed.append(command)
        self.tenants.append(tenant_ctx)
        if self._specs is not None:
            return self._specs
        return [_cron(), _cron("0 17 * * 5")]  # two schedules


class _FakeStore:
    def __init__(self, *, fail_after: int | None = None) -> None:
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self._fail_after = fail_after

    async def create_async(self, **kwargs: Any) -> str:
        if self._fail_after is not None and len(self.created) >= self._fail_after:
            from app.triggers.quota import TriggerQuotaExceeded

            raise TriggerQuotaExceeded("Trigger quota exceeded")
        self.created.append(kwargs)
        return f"sched-{len(self.created)}"

    async def delete_async(self, schedule_id: str, *, tenant_ctx: Any) -> bool:
        self.deleted.append(schedule_id)
        return True


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
    # TRG-10/11: the parse is charged to the tenant and the plan quota applies.
    assert sched.tenants[0].tenant_id == "t1"
    assert all(c["quota_plan"] == "professional" for c in store.created)


@pytest.mark.parametrize(
    "spec",
    [
        TriggerSpec(trigger_type=TriggerType.S3_EVENT, s3_bucket="b"),  # no runtime
        TriggerSpec(trigger_type=TriggerType.ONCE, description="x"),  # no fire time
    ],
)
async def test_create_schedule_refuses_a_spec_that_cannot_fire(spec: TriggerSpec) -> None:
    store = _FakeStore()
    svc = ChatService(nl_scheduler=_FakeScheduler([_cron(), spec]), schedule_store=store)

    with pytest.raises(ValueError, match=spec.trigger_type.value):
        await svc.create_schedule(tenant_ctx=_ctx(), message="do it")
    assert store.created == []  # the valid cron was not created either


async def test_create_schedule_nothing_understood_is_an_error() -> None:
    store = _FakeStore()
    svc = ChatService(nl_scheduler=_FakeScheduler([]), schedule_store=store)
    with pytest.raises(ValueError, match="no schedule"):
        await svc.create_schedule(tenant_ctx=_ctx(), message="hmm")
    assert store.created == []


async def test_create_schedule_quota_refusal_rolls_back_the_batch() -> None:
    from app.triggers.quota import TriggerQuotaExceeded

    store = _FakeStore(fail_after=1)
    svc = ChatService(nl_scheduler=_FakeScheduler(), schedule_store=store)
    with pytest.raises(TriggerQuotaExceeded):
        await svc.create_schedule(tenant_ctx=_ctx(), message="twice a week")
    assert store.deleted == ["sched-1"]


async def test_create_schedule_without_deps_is_explicit_error() -> None:
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
