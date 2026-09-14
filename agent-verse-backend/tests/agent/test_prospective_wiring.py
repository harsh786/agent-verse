"""T3 — prospective memory: read-only recall + planner block."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta


from app.agent.prospective_wiring import pending_intentions_block
from app.memory.prospective import (
    ProspectiveMemory,
    ProspectiveMemoryService,
    prospective_id,
)

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _item(intention: str, *, due_offset_h: float, state: str = "pending") -> ProspectiveMemory:
    key = intention
    return ProspectiveMemory(
        memory_id=prospective_id("t1", key),
        tenant_id="t1",
        intention=intention,
        due_at=_NOW + timedelta(hours=due_offset_h),
        expires_at=_NOW + timedelta(days=30),
        state=state,  # type: ignore[arg-type]
        source_goal_id="g1",
        source_execution_id="e1",
        policy_snapshot={},
        classification="internal",
        idempotency_key=key,
    )


async def test_list_active_is_readonly_and_due_first() -> None:
    svc = ProspectiveMemoryService()
    await svc.create(_item("call the vendor", due_offset_h=-1))  # already due
    await svc.create(_item("review report", due_offset_h=48))  # upcoming
    await svc.create(_item("done thing", due_offset_h=-2, state="completed"))  # terminal

    active = await svc.list_active("t1", now=_NOW)
    intentions = [i.intention for i in active]
    assert "done thing" not in intentions  # terminal excluded
    assert intentions == ["call the vendor", "review report"]  # due-first
    # read-only: states unchanged (no lease)
    again = await svc.list_active("t1", now=_NOW)
    assert [i.state for i in again] == [i.state for i in active]


async def test_list_active_excludes_expired_and_other_tenants() -> None:
    svc = ProspectiveMemoryService()
    expired = _item("stale", due_offset_h=-100).model_copy(
        update={"expires_at": _NOW - timedelta(hours=1)}
    )
    await svc.create(expired)
    assert await svc.list_active("t1", now=_NOW) == ()
    assert await svc.list_active("other", now=_NOW) == ()


def test_block_renders_due_first_and_empty_when_none() -> None:
    assert pending_intentions_block([], _NOW) == ""
    block = pending_intentions_block(
        [_item("upcoming", due_offset_h=24), _item("overdue", due_offset_h=-3)],
        _NOW,
    )
    # due item leads and is marked DUE NOW
    assert block.splitlines()[0].startswith("- (DUE NOW) overdue")
    assert "upcoming" in block
