"""MEM-43: a due intention is submitted as a goal AT MOST ONCE.

A failed or lost ``complete`` after a successful submission used to re-submit
the same intention once the lease expired. Firing now checks for the goal the
intention already produced (by its memory id) before submitting, never lets a
``complete`` error escape, and moves an intention that keeps failing to
``failed`` after a capped number of attempts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.memory.prospective import ProspectiveMemory, ProspectiveMemoryService
from app.memory.prospective_runtime import (
    MAX_FIRE_ATTEMPTS,
    fire_due_intentions,
    intention_json,
)

T = "t-pm-once"
NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


def _item(mid: str = "m1") -> ProspectiveMemory:
    return ProspectiveMemory(
        memory_id=mid, tenant_id=T, intention="check the deploy", due_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(days=1), source_goal_id="", source_execution_id="",
        policy_snapshot={}, classification="internal", idempotency_key=mid,
    )


class _GoalBook:
    """Stands in for the goals table: what each intention produced."""

    def __init__(self) -> None:
        self.goals: dict[str, str] = {}
        self.submissions = 0

    async def submit(self, item: ProspectiveMemory) -> dict[str, Any]:
        self.submissions += 1
        gid = f"goal-{self.submissions}"
        self.goals[item.memory_id] = gid
        return {"goal_id": gid}


class _Service(ProspectiveMemoryService):
    def __init__(self, book: _GoalBook) -> None:
        super().__init__()
        self.book = book
        self.complete_failures = 0

    async def find_submitted_goal(self, tenant_id: str, memory_id: str) -> str | None:
        return self.book.goals.get(memory_id)

    async def complete(self, *a: Any, **kw: Any) -> ProspectiveMemory:
        if self.complete_failures:
            self.complete_failures -= 1
            raise ConnectionError("db went away")
        return await super().complete(*a, **kw)


async def test_lost_complete_never_resubmits() -> None:
    book = _GoalBook()
    svc = _Service(book)
    await svc.create(_item())
    svc.complete_failures = 1
    fired = await fire_due_intentions(svc, tenant_id=T, submit=book.submit, now=NOW)
    assert fired == [] and book.submissions == 1  # complete failed — and did not raise

    later = NOW + timedelta(minutes=10)  # the lease expired
    fired = await fire_due_intentions(svc, tenant_id=T, submit=book.submit, now=later)
    assert book.submissions == 1  # NOT submitted again
    assert [f.result for f in fired] == [{"goal_id": "goal-1", "deduplicated": True}]
    assert (await svc.get(T, "m1")).state == "completed"


async def test_repeated_submit_failures_end_in_failed() -> None:
    svc = ProspectiveMemoryService()
    await svc.create(_item())

    async def _boom(_item: ProspectiveMemory) -> dict[str, Any]:
        raise RuntimeError("daily goal limit reached")

    when = NOW
    for _ in range(MAX_FIRE_ATTEMPTS + 2):
        await fire_due_intentions(svc, tenant_id=T, submit=_boom, now=when)
        when += timedelta(minutes=10)
    item = await svc.get(T, "m1")
    assert item.state == "failed"
    assert item.attempts == MAX_FIRE_ATTEMPTS
    assert "daily goal limit" in item.result["error"]
    shown = intention_json(item)
    assert shown["state"] == "failed" and shown["attempts"] == MAX_FIRE_ATTEMPTS


async def test_failed_intentions_are_listed_on_request() -> None:
    svc = ProspectiveMemoryService()
    await svc.create(_item("ok"))
    await svc.create(_item("bad"))
    (leased_bad,) = [i for i in await svc.lease_due(T, now=NOW, lease_duration=timedelta(minutes=5))
                     if i.memory_id == "bad"]
    await svc.fail(T, "bad", fencing_token=leased_bad.fencing_token, error="boom")
    assert {i.memory_id for i in await svc.list_active(T, now=NOW)} == {"ok"}
    listed = await svc.list_active(T, now=NOW, include_failed=True)
    assert {i.memory_id for i in listed} == {"ok", "bad"}


async def test_api_lists_failed_intentions_with_attempts_on_request() -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.api.memory import router
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.middleware import TenantMiddleware

    ctx = TenantContext(tenant_id=T, plan=PlanTier.PROFESSIONAL, api_key_id="k")
    svc = ProspectiveMemoryService()
    now = datetime.now(UTC)
    await svc.create(_item("bad").model_copy(update={"expires_at": now + timedelta(days=1)}))
    (leased,) = await svc.lease_due(T, now=now, lease_duration=timedelta(minutes=5))
    await svc.fail(T, "bad", fencing_token=leased.fencing_token, error="boom")

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == "k-pm" else None

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    app.state.prospective_memory_service = svc
    app.state.db_session_factory = None
    h = {"X-API-Key": "k-pm"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/memory/prospective", headers=h)).json() == []
        (shown,) = (await c.get("/memory/prospective?include_failed=true", headers=h)).json()
    assert shown["state"] == "failed" and shown["attempts"] == 1
    assert shown["result"] == {"error": "boom"}


async def test_fail_with_stale_token_is_refused() -> None:
    svc = ProspectiveMemoryService()
    await svc.create(_item())
    await svc.lease_due(T, now=NOW, lease_duration=timedelta(minutes=5))
    with pytest.raises(RuntimeError):
        await svc.fail(T, "m1", fencing_token=999, error="x")
