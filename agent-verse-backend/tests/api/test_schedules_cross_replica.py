"""Regression: ``/schedules`` must read and write through the durable store.

Old bug: the ``/schedules`` handlers (get, list, analytics, pause, resume, fire,
the alert webhook and ``POST /webhooks/{token}``) read ``store.get`` /
``store.list_all`` / ``store.pause`` — this process's in-memory dict only. On a
multi-replica deployment a schedule created on replica A was a 404 (or missing
from the list) on replica B, pause/resume from B never reached Postgres, and an
inbound webhook landing on B was "Unknown webhook token". A durable-store
outage also silently served the (possibly stale) cache.

Here two apps — two "replicas" — each have their own ``ScheduleStore`` (own
cache) over ONE shared fake ``schedules`` table.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.sql.dml import Delete, Update
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, TextClause
from sqlalchemy.sql.selectable import Select

from app.api.schedules import router as schedules_router
from app.api.schedules import webhooks_router
from app.db.models.scheduling import Schedule
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from app.triggers.store import ScheduleStore

_CTX_A = TenantContext(tenant_id="tenant-a", plan=PlanTier.PROFESSIONAL, api_key_id="ka")
_CTX_B = TenantContext(tenant_id="tenant-b", plan=PlanTier.PROFESSIONAL, api_key_id="kb")
_KEYS = {"key-a": _CTX_A, "key-b": _CTX_B}
_H = {"X-API-Key": "key-a"}


# ── A tiny shared "Postgres" for the schedules table ────────────────────────


class _Table:
    def __init__(self) -> None:
        self.rows: dict[str, Schedule] = {}
        self.fail = False


def _criteria(stmt: Any) -> dict[str, Any]:
    where = stmt.whereclause
    if where is None:
        return {}
    clauses = list(where.clauses) if isinstance(where, BooleanClauseList) else [where]
    out: dict[str, Any] = {}
    for clause in clauses:
        assert isinstance(clause, BinaryExpression), clause
        out[clause.left.key] = clause.right.value  # type: ignore[attr-defined]
    return out


def _matches(row: Schedule, crit: dict[str, Any]) -> bool:
    return all(getattr(row, key) == value for key, value in crit.items())


class _Result:
    def __init__(self, rows: list[Any] | None = None, rowcount: int = 0, scalar: Any = None):
        self._rows = rows or []
        self.rowcount = rowcount
        self._scalar = scalar

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar_one(self) -> Any:
        return self._scalar

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None


class _Begin:
    def __init__(self, session: _Session) -> None:
        self._session = session

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, exc_type: Any, *_: Any) -> None:
        if exc_type is None:
            await self._session.flush()
        self._session.pending.clear()


class _Session:
    def __init__(self, table: _Table) -> None:
        self.table = table
        self.pending: list[Schedule] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    def begin(self) -> _Begin:
        return _Begin(self)

    def add(self, row: Schedule) -> None:
        self.pending.append(row)

    async def flush(self) -> None:
        if self.table.fail:
            raise OSError("db down")
        for row in self.pending:
            row.created_at = row.created_at or datetime.now(UTC)
            self.table.rows[row.id] = row
        self.pending.clear()

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> _Result:
        if self.table.fail:
            raise OSError("db down")
        rows = self.table.rows
        if isinstance(stmt, TextClause):
            sql = str(stmt)
            if "count(*)" in sql:
                tid = (params or {})["t"]
                return _Result(scalar=sum(1 for r in rows.values() if r.tenant_id == tid))
            if "webhook_token = :tok" in sql:
                tok = (params or {})["tok"]
                hit = [(r.tenant_id, r.webhook_token) for r in rows.values() if r.webhook_token == tok]
                return _Result(rows=hit[:1])
            return _Result()  # set_config / advisory lock
        if isinstance(stmt, Select):
            crit = _criteria(stmt)
            return _Result(rows=[r for r in rows.values() if _matches(r, crit)])
        if isinstance(stmt, Update):
            crit = _criteria(stmt)
            hit = [r for r in rows.values() if _matches(r, crit)]
            for row in hit:
                for col, val in stmt._values.items():  # type: ignore[attr-defined]
                    setattr(row, getattr(col, "key", col), getattr(val, "value", val))
            return _Result(rowcount=len(hit))
        if isinstance(stmt, Delete):
            crit = _criteria(stmt)
            hit = [key for key, r in rows.items() if _matches(r, crit)]
            for key in hit:
                del rows[key]
            return _Result(rowcount=len(hit))
        raise AssertionError(f"unexpected statement {stmt!r}")


def _replica(table: _Table, dispatcher: Any | None = None) -> tuple[FastAPI, TestClient]:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _KEYS.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(schedules_router)
    app.include_router(webhooks_router)
    app.state.schedule_store = ScheduleStore(db_session_factory=lambda: _Session(table))
    app.state.trigger_dispatcher = dispatcher or _dispatcher()
    return app, TestClient(app)


def _dispatcher() -> AsyncMock:
    d = AsyncMock()
    d.dispatch.return_value = SimpleNamespace(
        goal_id="goal-xyz", goal_created=True, skip_reason=None, fired_at="now"
    )
    return d


def _create(client: TestClient, trigger_type: str = "webhook") -> dict[str, Any]:
    body: dict[str, Any] = {"trigger_type": trigger_type, "goal_template": "do it", "name": "n"}
    if trigger_type == "cron":
        body["cron_expr"] = "0 9 * * *"
    resp = client.post("/schedules", json=body, headers=_H)
    assert resp.status_code == 201, resp.text
    return resp.json()  # type: ignore[no-any-return]


# ── Cross-replica visibility ─────────────────────────────────────────────────


def test_schedule_created_on_a_is_visible_on_b() -> None:
    table = _Table()
    _, a = _replica(table)
    _, b = _replica(table)
    sid = _create(a, "cron")["schedule_id"]
    assert sid in table.rows

    got = b.get(f"/schedules/{sid}", headers=_H)
    assert got.status_code == 200, got.text
    assert got.json()["schedule_id"] == sid

    listed = b.get("/schedules", headers=_H)
    assert listed.status_code == 200
    assert [r["schedule_id"] for r in listed.json()] == [sid]

    analytics = b.get("/schedules/analytics", headers=_H)
    assert analytics.status_code == 200
    assert analytics.json()["total"] == 1


def test_pause_and_resume_on_b_persist_durably_and_are_seen_on_a() -> None:
    table = _Table()
    _, a = _replica(table)
    _, b = _replica(table)
    sid = _create(a, "cron")["schedule_id"]
    a.get(f"/schedules/{sid}", headers=_H)  # warm A's cache with paused=False

    assert b.post(f"/schedules/{sid}/pause", headers=_H).status_code == 200
    assert table.rows[sid].paused is True  # what the Celery beat reads
    assert a.get(f"/schedules/{sid}", headers=_H).json()["paused"] is True

    assert b.post(f"/schedules/{sid}/resume", headers=_H).status_code == 200
    assert table.rows[sid].paused is False
    assert a.get(f"/schedules/{sid}", headers=_H).json()["paused"] is False


def test_fire_on_b_finds_schedule_created_on_a() -> None:
    table = _Table()
    dispatcher = _dispatcher()
    _, a = _replica(table)
    _, b = _replica(table, dispatcher)
    sid = _create(a)["schedule_id"]

    resp = b.post(f"/schedules/{sid}/fire", headers=_H)
    assert resp.status_code == 202, resp.text
    assert resp.json()["goal_id"] == "goal-xyz"
    dispatcher.dispatch.assert_awaited_once()


def test_webhook_on_b_resolves_token_created_on_a() -> None:
    table = _Table()
    dispatcher = _dispatcher()
    app_a, a = _replica(table)
    app_b, b = _replica(table, dispatcher)
    rec = _create(a)
    token = rec["spec"]["webhook_token"]
    assert table.rows[rec["schedule_id"]].webhook_token == token

    resp = b.post(f"/webhooks/{token}", json={"x": 1}, headers=_H)
    assert resp.status_code == 202, resp.text
    assert resp.json()["schedule_id"] == rec["schedule_id"]
    dispatcher.dispatch.assert_awaited_once()

    # Another tenant presenting the token finds nothing.
    other = b.post(f"/webhooks/{token}", json={}, headers={"X-API-Key": "key-b"})
    assert other.status_code == 404

    # Paused on A → B refuses the delivery (the pause is durable, not A-local).
    a.post(f"/schedules/{rec['schedule_id']}/pause", headers=_H)
    paused = b.post(f"/webhooks/{token}", json={}, headers=_H)
    assert paused.status_code == 409
    assert not hasattr(app_a.state, "_webhook_tokens")
    assert not hasattr(app_b.state, "_webhook_tokens")


def test_alert_webhook_on_b_accepts_schedule_created_on_a() -> None:
    table = _Table()
    _, a = _replica(table)
    app_b, b = _replica(table)
    redis = AsyncMock()
    app_b.state.pools = SimpleNamespace(redis=redis)
    sid = _create(a, "cron")["schedule_id"]

    resp = b.post(
        f"/webhooks/alerts/datadog?schedule_id={sid}", json={"alert": 1}, headers=_H
    )
    assert resp.status_code == 200, resp.text
    redis.set.assert_awaited_once()


def test_delete_on_b_is_durable_and_a_stops_serving_its_cache() -> None:
    table = _Table()
    _, a = _replica(table)
    _, b = _replica(table)
    sid = _create(a, "cron")["schedule_id"]
    assert a.get(f"/schedules/{sid}", headers=_H).status_code == 200

    assert b.delete(f"/schedules/{sid}", headers=_H).status_code == 204
    assert sid not in table.rows
    assert a.get(f"/schedules/{sid}", headers=_H).status_code == 404
    assert a.get("/schedules", headers=_H).json() == []
    assert a.post(f"/schedules/{sid}/pause", headers=_H).status_code == 404
    assert a.delete(f"/schedules/{sid}", headers=_H).status_code == 404


def test_other_tenant_cannot_see_schedule() -> None:
    table = _Table()
    _, a = _replica(table)
    _, b = _replica(table)
    sid = _create(a, "cron")["schedule_id"]
    other = {"X-API-Key": "key-b"}
    assert b.get(f"/schedules/{sid}", headers=other).status_code == 404
    assert b.post(f"/schedules/{sid}/pause", headers=other).status_code == 404
    assert table.rows[sid].paused is False


# ── Fail closed ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/schedules/{sid}"),
        ("get", "/schedules"),
        ("get", "/schedules/analytics"),
        ("post", "/schedules/{sid}/pause"),
        ("post", "/schedules/{sid}/resume"),
        ("post", "/schedules/{sid}/fire"),
        ("delete", "/schedules/{sid}"),
        ("post", "/webhooks/{token}"),
        ("post", "/webhooks/alerts/datadog?schedule_id={sid}"),
    ],
)
def test_durable_store_outage_is_503_not_a_stale_cache(method: str, path: str) -> None:
    table = _Table()
    dispatcher = _dispatcher()
    app, a = _replica(table, dispatcher)
    app.state.pools = SimpleNamespace(redis=AsyncMock())
    rec = _create(a)  # A's cache now holds the schedule
    table.fail = True

    url = path.format(sid=rec["schedule_id"], token=rec["spec"]["webhook_token"])
    kwargs: dict[str, Any] = {"headers": _H}
    if method == "post":
        kwargs["json"] = {}
    resp = getattr(a, method)(url, **kwargs)
    assert resp.status_code == 503, resp.text
    dispatcher.dispatch.assert_not_awaited()


def test_create_during_outage_is_503_and_not_cached() -> None:
    table = _Table()
    app, a = _replica(table)
    table.fail = True
    resp = a.post(
        "/schedules", json={"trigger_type": "cron", "cron_expr": "0 9 * * *"}, headers=_H
    )
    assert resp.status_code == 503, resp.text
    assert app.state.schedule_store.list_all(tenant_ctx=_CTX_A) == []
