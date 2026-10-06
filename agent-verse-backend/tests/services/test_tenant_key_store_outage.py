"""a08-F194-05: a key-store (Postgres) outage is a retryable 503, never a 401.

``_db_resolve_by_hash`` returned ``None`` on any DB error, so every uncached
API key answered 401 "invalid key" during an outage — clients treat that as a
bad key (drop it, log out, rotate). The lookup now raises
``KeyStoreUnavailableError`` and ``TenantMiddleware`` answers 503 with
``Retry-After``; a genuinely unknown key is still a 401.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.tenant_service import KeyStoreUnavailableError, TenantService
from app.tenancy.middleware import TenantMiddleware


class _DownSession:
    async def __aenter__(self) -> _DownSession:
        raise ConnectionRefusedError("postgres is down")

    async def __aexit__(self, *args: object) -> None:
        return None


class _EmptySession:
    """A reachable DB in which the presented key hash has no row."""

    async def __aenter__(self) -> _EmptySession:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    def begin(self) -> _EmptySession:
        return self

    async def execute(self, *args: object, **kwargs: object) -> Any:
        class _R:
            def first(self) -> None:
                return None

        return _R()


async def test_db_resolve_raises_key_store_unavailable_on_db_error() -> None:
    svc = TenantService(db_session_factory=_DownSession)
    with pytest.raises(KeyStoreUnavailableError) as exc:
        await svc._db_resolve_by_hash("some-hash")
    assert exc.value.http_status == 503
    assert exc.value.retryable is True


async def test_resolve_api_key_propagates_the_outage() -> None:
    svc = TenantService(db_session_factory=_DownSession)
    with pytest.raises(KeyStoreUnavailableError):
        await svc.resolve_api_key("av_free_not-a-real-key")


def _app(svc: TenantService) -> FastAPI:
    app = FastAPI()
    app.state.tenant_service = svc
    app.add_middleware(TenantMiddleware, key_resolver=svc.resolve_api_key)

    @app.get("/goals")
    async def goals() -> dict[str, str]:
        return {"ok": "yes"}

    return app


def test_middleware_answers_503_when_the_key_store_is_down() -> None:
    client = TestClient(_app(TenantService(db_session_factory=_DownSession)))
    resp = client.get("/goals", headers={"X-API-Key": "av_free_not-a-real-key"})
    assert resp.status_code == 503
    assert resp.headers.get("retry-after") == "5"
    body = resp.json()["error"]
    assert body["code"] == "KEY_STORE_UNAVAILABLE"
    assert body["retryable"] is True


def test_unknown_key_with_a_healthy_store_is_still_401() -> None:
    client = TestClient(_app(TenantService(db_session_factory=_EmptySession)))
    resp = client.get("/goals", headers={"X-API-Key": "av_free_not-a-real-key"})
    assert resp.status_code == 401
