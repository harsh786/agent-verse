"""TRG-15: beat discovery is O(1) statements, not one RLS query per tenant.

``_load_db_schedules`` listed every active tenant and ran one query per tenant
each minute, and ``_persist_next_evaluations`` opened one transaction per
schedule. Both now issue a single statement through the maintenance session.
(The real claim/lease SQL runs on Postgres in
tests/scaling/test_beat_discovery_integration.py.)
"""

from __future__ import annotations

import datetime as dt
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.scaling import tasks


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> Any:
        rows = self._rows

        class _M:
            def all(self) -> list[dict[str, Any]]:
                return rows

        return _M()


class _Session:
    def __init__(self, log: list[str], rows: list[dict[str, Any]]) -> None:
        self.log = log
        self.rows = rows

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = str(stmt)
        if "row_security" not in sql:
            self.log.append(sql)
        return _Result(self.rows)

    def begin(self) -> Any:
        @asynccontextmanager
        async def _tx() -> Any:
            yield self

        return _tx()


def _factory(log: list[str], rows: list[dict[str, Any]]) -> Any:
    @asynccontextmanager
    async def _open() -> Any:
        yield _Session(log, rows)

    return _open


def _row(tenant: str, sid: str) -> dict[str, Any]:
    return {
        "id": sid,
        "tenant_id": tenant,
        "goal_id_template": "g",
        "agent_id": None,
        "trigger_type": "cron",
        "cron_expression": "* * * * *",
        "timezone": "UTC",
        "interval_seconds": 0,
        "paused": False,
        "config": {},
        "last_fired_at": None,
        "next_fire_at": None,
    }


@pytest.mark.asyncio
async def test_discovery_is_one_statement_for_thousands_of_tenants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log: list[str] = []
    rows = [_row(f"t{i}", f"s{i}") for i in range(2000)]  # 2000 tenants with due rows
    monkeypatch.setattr("app.db.session.get_system_session_factory", lambda: _factory(log, rows))
    found = await tasks._load_db_schedules(dt.datetime(2026, 10, 2, 12, 0))
    assert found is not None and len(found) == 2000
    assert len(log) == 1
    sql = log[0]
    assert "FOR UPDATE OF d SKIP LOCKED" in sql and "RETURNING" in sql
    assert "tenants" in sql  # inactive tenants' schedules are excluded in SQL


@pytest.mark.asyncio
async def test_next_fire_write_back_is_one_statement(monkeypatch: pytest.MonkeyPatch) -> None:
    log: list[str] = []
    monkeypatch.setattr("app.db.session.get_system_session_factory", lambda: _factory(log, []))
    later = dt.datetime(2026, 10, 2, 13, 0, tzinfo=dt.UTC)
    await tasks._persist_next_evaluations([(f"t{i}", f"s{i}", later) for i in range(5000)])
    assert len(log) == 1 and "unnest" in log[0]


@pytest.mark.asyncio
async def test_db_error_returns_none_for_the_redis_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> Any:
        raise RuntimeError("no maintenance db")

    monkeypatch.setattr("app.db.session.get_system_session_factory", _boom)
    assert await tasks._load_db_schedules() is None
