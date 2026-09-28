"""SLA stats + enforce_hitl_sla run on approval_requests (the table actually written).

``hitl_approval_requests`` is never written, so /approvals/sla-stats was always
zeros and enforce_hitl_sla was a no-op.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from httpx import ASGITransport, AsyncClient

from app.api.governance import router
from tests.governance._router_app import make_app


class _Res:
    def __init__(self, rows: list[Any] | None = None, one: Any = None) -> None:
        self._rows = rows or []
        self._one = one

    def fetchall(self) -> list[Any]:
        return self._rows

    def fetchone(self) -> Any:
        return self._one


def _session(execute: Any) -> Any:
    s = MagicMock()
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=False)
    b = MagicMock()
    b.__aenter__ = AsyncMock(return_value=s)
    b.__aexit__ = AsyncMock(return_value=False)
    s.begin = MagicMock(return_value=b)
    s.execute = AsyncMock(side_effect=execute)
    return s


async def test_sla_stats_read_approval_requests_under_rls() -> None:
    seen: list[tuple[str, Any]] = []

    async def execute(q: Any, params: Any = None) -> _Res:
        sql = str(q)
        seen.append((sql, params))
        if "FROM approval_requests" in sql:
            return _Res(one=(2, 5, 1, 1, 3, 4, 2, 90.0))
        return _Res()

    sess = _session(execute)
    app = make_app(router)
    app.state.db_session_factory = lambda: sess
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/governance/approvals/sla-stats")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending"] == 2 and body["approved"] == 5 and body["rejected"] == 1
    assert body["escalated"] == 3 and body["within_sla"] == 4 and body["breached_sla"] == 2
    assert body["avg_resolution_seconds"] == 90.0
    sqls = [q for q, _ in seen]
    assert not any("hitl_approval_requests" in q for q in sqls)
    assert "set_config('app.tenant_id'" in sqls[0] and seen[0][1] == {"tid": "t-gov"}


async def test_sla_stats_db_error_is_503_not_zeros() -> None:
    async def execute(q: Any, params: Any = None) -> _Res:
        if "approval_requests" in str(q):
            raise RuntimeError("db down")
        return _Res()

    sess = _session(execute)
    app = make_app(router)
    app.state.db_session_factory = lambda: sess
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/governance/approvals/sla-stats")
    assert r.status_code == 503


async def test_enforce_sla_auto_denies_escalates_and_releases_waiters() -> None:
    from app.governance.hitl_sla import enforce_sla

    async def execute(q: Any, params: Any = None) -> _Res:
        sql = str(q)
        assert "hitl_approval_requests" not in sql
        if "status = 'rejected'" in sql:
            return _Res(rows=[("r-deny", "t1")])
        if "SET escalated_at = NOW()" in sql:
            return _Res(rows=[("r-esc", "t1", False), ("r-wants-approve", "t2", True)])
        return _Res()

    redis = MagicMock()
    redis.rpush = AsyncMock()
    redis.expire = AsyncMock()
    out = await enforce_sla(_session(execute), redis=redis)
    assert out == {
        "auto_denied": 1,
        "escalated": 2,
        "auto_approve_refused": 1,
        "waiters_released": 1,
    }
    key, payload = redis.rpush.call_args.args
    assert key == "hitl_result:r-deny" and '"rejected"' in payload


def test_enforce_hitl_sla_task_uses_real_table(monkeypatch: Any) -> None:
    import app.db.session as db_session
    from app.scaling import tasks

    calls: list[str] = []

    async def execute(q: Any, params: Any = None) -> _Res:
        calls.append(str(q))
        if "status = 'rejected'" in str(q):
            return _Res(rows=[("r1", "t1")])
        return _Res()

    sess = _session(execute)
    monkeypatch.setattr(db_session, "get_system_session_factory", lambda: (lambda: sess))
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    out = tasks.enforce_hitl_sla()
    assert out.get("auto_denied") == 1, out
    assert any("FROM approval_requests" in q for q in calls)
