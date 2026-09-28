"""With a database configured, the memory API must not fall back to the cache.

Regression: DELETE /memory and DELETE /memory/{id} swallowed DB errors, edited
the per-process cache and answered 204/200 — a faked GDPR erasure (the rows
survived in Postgres and on every other replica). GET /memory likewise served
the replica-local cache as truth on a DB error.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.memory import router as memory_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-mem-dbauth", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_KEY = "ak_mem_dbauth"
_H = {"X-API-Key": _KEY}


class _BrokenDB:
    """Session factory whose sessions fail on enter (DB down)."""

    def __call__(self) -> _BrokenDB:
        return self

    async def __aenter__(self) -> Any:
        raise RuntimeError("connection refused")

    async def __aexit__(self, *a: Any) -> None:
        return None


def _app(db: Any) -> tuple[FastAPI, MagicMock]:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(memory_router)
    app.state.db_session_factory = db
    ltm = MagicMock()
    ltm._memories = {
        _CTX.tenant_id: [SimpleNamespace(id="m1", memory_id="m1", content="cached only")]
    }
    ltm.delete = MagicMock(return_value=True)
    app.state.long_term_memory = ltm
    return app, ltm


def test_delete_by_id_db_failure_is_503_and_cache_untouched() -> None:
    app, ltm = _app(_BrokenDB())
    r = TestClient(app).delete("/memory/m1", headers=_H)
    assert r.status_code == 503
    ltm.delete.assert_not_called()


def test_clear_all_db_failure_is_503_and_cache_untouched() -> None:
    app, ltm = _app(_BrokenDB())
    r = TestClient(app).delete("/memory", headers=_H)
    assert r.status_code == 503
    assert _CTX.tenant_id in ltm._memories


def test_delete_long_term_db_failure_is_503() -> None:
    app, ltm = _app(_BrokenDB())
    r = TestClient(app).delete("/memory/long-term/m1", headers=_H)
    assert r.status_code == 503
    ltm.delete.assert_not_called()


def test_list_db_failure_is_503_not_cache() -> None:
    app, _ = _app(_BrokenDB())
    r = TestClient(app).get("/memory", headers=_H)
    assert r.status_code == 503


def test_no_db_configured_uses_cache_without_touching_global_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom() -> None:
        raise AssertionError("must not build a DB engine when none is configured")

    monkeypatch.setattr("app.db.session.get_session_factory", _boom)
    app, ltm = _app(None)
    client = TestClient(app)
    listed = client.get("/memory", headers=_H)
    assert listed.status_code == 200
    assert listed.json()[0]["content"] == "cached only"
    assert client.delete("/memory/m1", headers=_H).status_code == 200
    ltm.delete.assert_called_once()
