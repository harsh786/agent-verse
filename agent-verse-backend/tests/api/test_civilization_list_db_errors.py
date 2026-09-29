"""Civilization list endpoints must not turn a DB failure into an empty list.

Regression: GET /civilizations, /{id}/members and /{id}/spawns caught every
DB exception (and a missing DB) and answered 200 ``[]`` -- indistinguishable
from "this tenant genuinely has no civilizations / members / spawns". A DB
failure is now a 503 with a clear detail; ``[]`` only when the query ran and
returned no rows.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.civilization import router as civ_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-civ-dberr", plan=PlanTier.ENTERPRISE, api_key_id="k")
_KEY = "av_test_civ_dberr"
_H = {"X-API-Key": _KEY}

_PATHS = ["/civilizations", "/civilizations/civ-1/members", "/civilizations/civ-1/spawns"]


def _session_factory(*, rows: list[Any] | None = None, error: Exception | None = None) -> Any:
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    if error is not None:
        session.execute = AsyncMock(side_effect=error)
    else:
        result = MagicMock()
        result.fetchall.return_value = rows or []
        session.execute = AsyncMock(return_value=result)
    return MagicMock(return_value=session)


def _client(db: Any) -> TestClient:
    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(civ_router)
    settings = MagicMock()
    settings.civilization_enabled = True
    app.state.settings = settings
    app.state.db_session_factory = db
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _no_rls(monkeypatch: pytest.MonkeyPatch) -> None:
    """The RLS helper issues its own SET LOCAL; not what these tests exercise."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop(session: Any, tenant_id: str) -> Any:
        yield

    monkeypatch.setattr("app.api.civilization._rls_ctx", _noop)


@pytest.mark.parametrize("path", _PATHS)
def test_db_error_is_503_not_an_empty_list(path: str) -> None:
    client = _client(_session_factory(error=RuntimeError("connection refused")))
    resp = client.get(path, headers=_H)
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "unavailable" in detail.lower()
    assert "connection refused" not in detail  # driver text stays server-side


@pytest.mark.parametrize("path", _PATHS)
def test_no_db_is_503_not_an_empty_list(path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.api.civilization._get_db", lambda request: None)
    resp = _client(None).get(path, headers=_H)
    assert resp.status_code == 503


@pytest.mark.parametrize("path", _PATHS)
def test_genuinely_empty_result_is_200_empty_list(path: str) -> None:
    resp = _client(_session_factory(rows=[])).get(path, headers=_H)
    assert resp.status_code == 200
    assert resp.json() == []
