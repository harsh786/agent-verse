"""TRG-30: schedule listing paginates in SQL and the DB-backed cache is bounded.

GET /schedules loaded every tenant schedule and sliced in Python, and every DB
read wrote its rows into an unbounded per-process dict.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.tenancy.context import PlanTier, TenantContext
from app.triggers import store as store_mod
from app.triggers.models import TriggerSpec, TriggerType
from app.triggers.store import ScheduleStore

_CTX = TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k")


def _row(i: int) -> Any:
    return SimpleNamespace(
        id=f"s{i:04d}",
        tenant_id="t1",
        agent_id=None,
        goal_id_template="g",
        trigger_type="interval",
        cron_expression="",
        timezone="UTC",
        interval_seconds=3600,
        webhook_token="",
        event_channel="",
        fire_at_iso="",
        condition="",
        description="",
        config={},
        paused=False,
        webhook_signature_secret_enc="",
        webhook_signature_secret_prev_enc="",
        webhook_secret_grace_until=None,
        created_at=None,
        last_fired_at=None,
        next_fire_at=None,
    )


class _Session:
    def __init__(self, log: list[str], rows: list[Any]) -> None:
        self._log = log
        self._rows = rows

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    def begin(self) -> _Session:
        return self

    async def flush(self) -> None:
        return None

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        sql = (
            str(stmt.compile(compile_kwargs={"literal_binds": True}))
            if hasattr(stmt, "compile")
            else str(stmt)
        )
        self._log.append(sql)
        rows = self._rows
        if "LIMIT" in sql and "FROM schedules" in sql:
            rows = rows[:10]
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(rows)))


async def test_list_all_async_pushes_limit_and_offset_into_sql() -> None:
    log: list[str] = []
    rows = [_row(i) for i in range(50)]
    store = ScheduleStore(db_session_factory=lambda: _Session(log, rows))

    page = await store.list_all_async(tenant_ctx=_CTX, strict=True, limit=10, offset=20)

    schedule_sql = [q for q in log if "FROM schedules" in q]
    assert schedule_sql and "LIMIT 10" in schedule_sql[-1] and "OFFSET 20" in schedule_sql[-1]
    assert "ORDER BY" in schedule_sql[-1]
    assert len(page) == 10


async def test_db_backed_cache_is_bounded(monkeypatch: Any) -> None:
    monkeypatch.setattr(store_mod, "_CACHE_MAX_ENTRIES", 5)
    rows = [_row(i) for i in range(50)]
    store = ScheduleStore(db_session_factory=lambda: _Session([], rows))

    await store.list_all_async(tenant_ctx=_CTX, strict=True)

    assert len(store._data) <= 5


async def test_in_memory_store_is_not_bounded(monkeypatch: Any) -> None:
    """Without a DB the cache IS the store and must never evict."""
    monkeypatch.setattr(store_mod, "_CACHE_MAX_ENTRIES", 5)
    store = ScheduleStore()
    for _ in range(8):
        await store.create_async(
            goal_id="g",
            spec=TriggerSpec(trigger_type=TriggerType.INTERVAL, interval_seconds=3600),
            tenant_ctx=_CTX,
        )
    assert len(store.list_all(tenant_ctx=_CTX)) == 8
    page = await store.list_all_async(tenant_ctx=_CTX, limit=3, offset=6)
    assert len(page) == 2
