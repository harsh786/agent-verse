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
