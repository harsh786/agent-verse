"""Regression: civilization controls answered "ok" for writes that failed, and a
failed constitution read turned throttle/adjust_budget into a destructive
overwrite (the whole constitution replaced by the one changed field).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.civilization import router
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-civ", plan=PlanTier.ENTERPRISE, api_key_id="k")


class _Result:
    def __init__(self, row: Any = None, rowcount: int = 1) -> None:
        self._row = row
        self.rowcount = rowcount

    def fetchone(self) -> Any:
        return self._row


class _Session:
    def __init__(self, db: _DB) -> None:
        self._db = db

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    def begin(self) -> Any:
        @asynccontextmanager
        async def _b() -> Any:
            yield None

        return _b()

    async def execute(self, stmt: Any, params: Any = None) -> _Result:
        sql = str(stmt)
        if "SELECT constitution" in sql:
            if self._db.read_fails:
                raise RuntimeError("read failed")
            return _Result(row=(self._db.constitution,) if self._db.exists else None)
        if "UPDATE civilizations" in sql:
            if self._db.write_fails:
                raise RuntimeError("write failed")
            self._db.written = json.loads(params["c"])
            return _Result(rowcount=1)
        return _Result()


class _DB:
    def __init__(self, **kw: Any) -> None:
        self.constitution = {"total_budget_usd": 10.0, "max_agents": 7}
        self.exists = kw.get("exists", True)
        self.read_fails = kw.get("read_fails", False)
        self.write_fails = kw.get("write_fails", False)
        self.written: dict | None = None

    def __call__(self) -> _Session:
        return _Session(self)


def _client(db: _DB) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def _t(request: Any, call_next: Any) -> Any:
        request.state.tenant = CTX
        return await call_next(request)

    app.include_router(router)
    app.state.db_session_factory = db
    return TestClient(app, raise_server_exceptions=False)


@asynccontextmanager
async def _no_rls(session: Any, tenant_id: str) -> Any:
    yield session


def _post(db: _DB, action: str, params: dict | None = None) -> Any:
    with (
        patch("app.api.civilization._require_feature_enabled", lambda r: None),
        patch("app.api.civilization._get_db", lambda r: db),
        patch("app.api.civilization._rls_ctx", _no_rls),
        patch("app.civilization.governor.Governor.pause", new_callable=AsyncMock),
    ):
        return _client(db).post(
            f"/civilizations/civ-1/controls/{action}", json={"params": params or {}}
        )


def test_adjust_budget_preserves_the_rest_of_the_constitution() -> None:
    db = _DB()
    r = _post(db, "adjust_budget", {"total_budget_usd": 50})
    assert r.status_code == 200, r.text
    assert db.written == {"total_budget_usd": 50.0, "max_agents": 7}


def test_failed_read_never_overwrites_the_constitution() -> None:
    db = _DB(read_fails=True)
    r = _post(db, "adjust_budget", {"total_budget_usd": 50})
    assert r.status_code == 503
    assert db.written is None


def test_failed_write_is_503_not_ok() -> None:
    assert _post(_DB(write_fails=True), "throttle", {"spawn_rate_limit_per_min": 3}).status_code == 503


def test_unknown_civilization_is_404() -> None:
    assert _post(_DB(exists=False), "pause").status_code == 404


def test_missing_or_bad_param_is_422() -> None:
    assert _post(_DB(), "throttle").status_code == 422
    assert _post(_DB(), "adjust_budget", {"total_budget_usd": "lots"}).status_code == 422


# ── a08-F182-02/03: pause/resume/kill never answer success for a no-op ──────


def _post_with_governor(path: str, **governor_patches: Any) -> Any:
    db = _DB()
    patches = [
        patch(f"app.civilization.governor.Governor.{name}", mock)
        for name, mock in governor_patches.items()
    ]
    with (
        patch("app.api.civilization._require_feature_enabled", lambda r: None),
        patch("app.api.civilization._get_db", lambda r: db),
        patch("app.api.civilization._rls_ctx", _no_rls),
    ):
        for p in patches:
            p.start()
        try:
            return _client(db).post(f"/civilizations/civ-1/{path}", json={"params": {}})
        finally:
            for p in patches:
                p.stop()


def test_pause_that_could_not_be_applied_is_503() -> None:
    from app.civilization.governor import CivilizationControlError

    failing = AsyncMock(side_effect=CivilizationControlError("db down"))
    assert _post_with_governor("controls/pause", pause=failing).status_code == 503
    assert _post_with_governor("controls/resume", resume=failing).status_code == 503


def test_kill_unknown_member_is_404() -> None:
    missing = AsyncMock(side_effect=LookupError("not a member"))
    assert _post_with_governor("agents/ghost/kill", kill_agent=missing).status_code == 404


def test_kill_that_could_not_be_applied_is_503() -> None:
    from app.civilization.governor import CivilizationControlError

    failing = AsyncMock(side_effect=CivilizationControlError("db down"))
    assert _post_with_governor("agents/a1/kill", kill_agent=failing).status_code == 503


def test_kill_reports_the_goals_it_cancelled() -> None:
    ok = AsyncMock(return_value={"goals_cancelled": 2, "signal_failures": 0})
    r = _post_with_governor("agents/a1/kill", kill_agent=ok)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "killed": "a1",
        "civilization_id": "civ-1",
        "goals_cancelled": 2,
        "signal_failures": 0,
    }
