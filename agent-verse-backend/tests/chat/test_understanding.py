"""World-class understanding: compound decomposition + durable multi-action fulfill."""

from __future__ import annotations

from typing import Any

from app.chat.service import ChatService
from app.chat.understanding import (
    GoalAction,
    QAAction,
    RememberAction,
    ScheduleAction,
    decompose,
)
from app.providers.fake import FakeProvider


async def test_decompose_splits_schedule_and_remember() -> None:
    acts = await decompose(
        "every tuesday at 18.02pm send me the sales summary and remember our q3 date is september 15"
    )
    kinds = [type(a).__name__ for a in acts]
    assert kinds == ["ScheduleAction", "RememberAction"]
    sched = acts[0]
    assert isinstance(sched, ScheduleAction) and sched.cron == "2 18 * * 2"
    rem = acts[1]
    assert isinstance(rem, RememberAction) and "q3 date is september 15" in rem.fact


async def test_decompose_one_time_dated_schedule() -> None:
    acts = await decompose("remind me at 6.11pm on 15 sept to call the dentist")
    assert len(acts) == 1 and isinstance(acts[0], ScheduleAction)
    assert acts[0].once is True and acts[0].cron == "11 18 15 9 *"


async def test_decompose_single_qa_and_goal() -> None:
    assert isinstance((await decompose("what is our refund policy?"))[0], QAAction)
    assert isinstance((await decompose("deploy the staging service"))[0], GoalAction)


class _FakeScheduleStore:
    def __init__(self) -> None:
        self.created: list[Any] = []
        self.quota_plans: list[str | None] = []

    async def create_async(  # type: ignore[no-untyped-def]
        self, *, goal_id, spec, tenant_ctx, agent_id, goal_template, quota_plan=None
    ):
        self.created.append(spec)
        self.quota_plans.append(quota_plan)
        return f"sched-{len(self.created)}"


async def test_afulfill_executes_schedule_and_remember_durably() -> None:
    remembered: list[str] = []

    async def _writer(fact: str, tenant_id: str) -> None:
        remembered.append(fact)

    store = _FakeScheduleStore()
    svc = ChatService(answer_generator=FakeProvider(responses=["ok"]), memory_writer=_writer)
    svc.attach_engine(nl_scheduler=object(), schedule_store=store)
    session = svc.create_session("t1")

    out = await svc.afulfill(
        session_id=session.id, tenant_id="t1",
        message="every Monday at 9am send the report and remember my seat preference is aisle",
    )
    assert set(out["actions"]) == {"schedule", "remember"}
    # Durable schedule really created, memory really written.
    assert len(store.created) == 1 and store.created[0].cron_expression == "0 9 * * 1"
    assert remembered == ["my seat preference is aisle"]
    # Combined reply mentions both, never raw JSON.
    assert "Scheduled" in out["reply"] and "remember" in out["reply"].lower()


async def test_afulfill_one_time_schedule_uses_fire_at() -> None:
    store = _FakeScheduleStore()
    svc = ChatService(answer_generator=FakeProvider(responses=["ok"]))
    svc.attach_engine(nl_scheduler=object(), schedule_store=store)
    session = svc.create_session("t1")
    await svc.afulfill(session_id=session.id, tenant_id="t1",
                       message="remind me on September 20th at 5pm to submit taxes")
    assert len(store.created) == 1
    spec = store.created[0]
    assert str(spec.trigger_type).endswith("ONCE") or spec.trigger_type == "once"
    assert spec.fire_at_iso  # a concrete one-time timestamp was computed


# --- a06-F102-08: the compound-turn schedule path shares POST /schedules' gates ---


async def test_afulfill_schedule_enforces_the_plan_trigger_quota() -> None:
    """The compound path passes quota_plan, so PLAN_MAX_TRIGGERS applies to it."""
    from app.tenancy.context import PlanTier, TenantContext

    store = _FakeScheduleStore()
    svc = ChatService(answer_generator=FakeProvider(responses=["ok"]))
    svc.attach_engine(nl_scheduler=object(), schedule_store=store)
    session = svc.create_session("t1")
    ctx = TenantContext(tenant_id="t1", api_key_id="k", plan=PlanTier.STARTER)
    await svc.afulfill(
        session_id=session.id, tenant_id="t1", tenant_ctx=ctx,
        message="every Monday at 9am send the report",
    )
    assert store.quota_plans == ["starter"]


async def test_afulfill_schedule_over_quota_is_reported_not_claimed() -> None:
    """A refused create never answers "I'll ..." (it looked scheduled)."""
    from app.triggers.quota import TriggerQuotaExceeded

    class _FullStore(_FakeScheduleStore):
        async def create_async(self, **kwargs: Any) -> str:  # type: ignore[override]
            raise TriggerQuotaExceeded("Trigger quota exceeded: plan 'free' allows 5 triggers")

    svc = ChatService(answer_generator=FakeProvider(responses=["ok"]))
    svc.attach_engine(nl_scheduler=object(), schedule_store=_FullStore())
    session = svc.create_session("t1")
    out = await svc.afulfill(
        session_id=session.id, tenant_id="t1",
        message="every Monday at 9am send the report",
    )
    assert "I'll" not in out["reply"]
    assert "could not schedule" in out["reply"].lower()
    assert "quota" in out["reply"].lower()


async def test_afulfill_schedule_refuses_an_uncreatable_spec() -> None:
    """creatable_error gates the compound path: a spec that would never fire is not stored."""
    import app.triggers.validation as validation

    store = _FakeScheduleStore()
    svc = ChatService(answer_generator=FakeProvider(responses=["ok"]))
    svc.attach_engine(nl_scheduler=object(), schedule_store=store)
    session = svc.create_session("t1")
    original = validation.creatable_error
    validation.creatable_error = lambda spec, *, plan="free": "cron: too frequent for plan"  # type: ignore[assignment]
    try:
        out = await svc.afulfill(
            session_id=session.id, tenant_id="t1",
            message="every Monday at 9am send the report",
        )
    finally:
        validation.creatable_error = original  # type: ignore[assignment]
    assert store.created == []
    assert "could not schedule" in out["reply"].lower()
    assert "too frequent" in out["reply"]


async def test_afulfill_schedule_unexpected_failure_is_not_claimed() -> None:
    class _BrokenStore(_FakeScheduleStore):
        async def create_async(self, **kwargs: Any) -> str:  # type: ignore[override]
            raise ConnectionError("db down at 10.0.0.5")

    svc = ChatService(answer_generator=FakeProvider(responses=["ok"]))
    svc.attach_engine(nl_scheduler=object(), schedule_store=_BrokenStore())
    session = svc.create_session("t1")
    out = await svc.afulfill(
        session_id=session.id, tenant_id="t1",
        message="every Monday at 9am send the report",
    )
    assert "I'll" not in out["reply"]
    assert "could not schedule" in out["reply"].lower()
    assert "10.0.0.5" not in out["reply"]  # driver detail stays in the log


async def test_afulfill_schedule_without_engine_does_not_claim_success() -> None:
    svc = ChatService(answer_generator=FakeProvider(responses=["ok"]))
    session = svc.create_session("t1")
    out = await svc.afulfill(
        session_id=session.id, tenant_id="t1",
        message="every Monday at 9am send the report",
    )
    assert "I'll" not in out["reply"]
