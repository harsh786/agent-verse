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

    async def create_async(self, *, goal_id, spec, tenant_ctx, agent_id, goal_template):  # type: ignore[no-untyped-def]
        self.created.append(spec)
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
