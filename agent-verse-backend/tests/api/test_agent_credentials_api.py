"""Tests for /agents/{agent_id}/keys — per-agent credential management API."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.agent_credentials_api import router as credentials_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-creds", plan=PlanTier.PROFESSIONAL, api_key_id="kid-creds")
_VALID_KEY = "av_test_creds_key"
_HEADERS = {"X-API-Key": _VALID_KEY}


def _make_app() -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(credentials_router)
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_app(), raise_server_exceptions=False)


def _patched_store(**overrides: Any):
    store = AsyncMock()
    store.create_key_async = AsyncMock(
        return_value=overrides.get(
            "create_key_async", {"key_id": "key-1", "api_key": "ak_secret"}
        )
    )
    store.list_for_agent_async = AsyncMock(
        return_value=overrides.get("list_for_agent_async", [])
    )
    store.revoke_async = AsyncMock(return_value=overrides.get("revoke_async", True))
    return store


# ---------------------------------------------------------------------------
# create_agent_key
# ---------------------------------------------------------------------------


def test_create_agent_key_requires_auth(client: TestClient) -> None:
    resp = client.post("/agents/agent-1/keys", json={"name": "k1"})
    assert resp.status_code == 401


def test_create_agent_key_success(client: TestClient) -> None:
    store = _patched_store(create_key_async={"key_id": "k1", "api_key": "secret"})
    with patch("app.auth.agent_credentials._agent_credential_store", store):
        resp = client.post(
            "/agents/agent-1/keys",
            json={"name": "my key", "denied_tools": ["dangerous_tool"]},
            headers=_HEADERS,
        )
    assert resp.status_code == 200
    assert resp.json() == {"key_id": "k1", "api_key": "secret"}
    store.create_key_async.assert_awaited_once()
    kwargs = store.create_key_async.call_args.kwargs
    assert kwargs["agent_id"] == "agent-1"
    assert kwargs["tenant_id"] == "tid-creds"
    assert kwargs["name"] == "my key"
    assert kwargs["denied_tools"] == ["dangerous_tool"]
    assert kwargs["created_by"] == "kid-creds"
    assert kwargs["expires_at"] is None


def test_create_agent_key_computes_expiry_from_days(client: TestClient) -> None:
    store = _patched_store()
    with patch("app.auth.agent_credentials._agent_credential_store", store):
        resp = client.post(
            "/agents/agent-1/keys",
            json={"name": "k", "expires_in_days": 30},
            headers=_HEADERS,
        )
    assert resp.status_code == 200
    kwargs = store.create_key_async.call_args.kwargs
    assert kwargs["expires_at"] is not None
    assert kwargs["expires_at"] > 0


def test_create_agent_key_validation_error_missing_name(client: TestClient) -> None:
    resp = client.post("/agents/agent-1/keys", json={}, headers=_HEADERS)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# list_agent_keys
# ---------------------------------------------------------------------------


def test_list_agent_keys_requires_auth(client: TestClient) -> None:
    resp = client.get("/agents/agent-1/keys")
    assert resp.status_code == 401


def test_list_agent_keys_success(client: TestClient) -> None:
    store = _patched_store(list_for_agent_async=[{"key_id": "k1"}, {"key_id": "k2"}])
    with patch("app.auth.agent_credentials._agent_credential_store", store):
        resp = client.get("/agents/agent-1/keys", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "agent-1"
    assert data["keys"] == [{"key_id": "k1"}, {"key_id": "k2"}]
    store.list_for_agent_async.assert_awaited_once_with("agent-1", "tid-creds")


# ---------------------------------------------------------------------------
# revoke_agent_key
# ---------------------------------------------------------------------------


def test_revoke_agent_key_requires_auth(client: TestClient) -> None:
    resp = client.delete("/agents/agent-1/keys/key-1")
    assert resp.status_code == 401


def test_revoke_agent_key_success(client: TestClient) -> None:
    store = _patched_store(revoke_async=True)
    with patch("app.auth.agent_credentials._agent_credential_store", store):
        resp = client.delete("/agents/agent-1/keys/key-1", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"key_id": "key-1", "status": "revoked"}
    store.revoke_async.assert_awaited_once_with("key-1", "agent-1", "tid-creds")


def test_revoke_agent_key_not_found(client: TestClient) -> None:
    store = _patched_store(revoke_async=False)
    with patch("app.auth.agent_credentials._agent_credential_store", store):
        resp = client.delete("/agents/agent-1/keys/key-missing", headers=_HEADERS)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# get_agent_manifest
# ---------------------------------------------------------------------------


def test_get_agent_manifest_without_agent_store_uses_bare_id(client: TestClient) -> None:
    resp = client.get("/agents/agent-1/keys/manifest", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["agent_id"] == "agent-1"
    assert data["_signed"] is True
    assert "_signature" in data


def test_get_agent_manifest_uses_agent_store_when_present() -> None:
    app = _make_app()
    agent_store = AsyncMock()
    agent_store.get_agent = AsyncMock(return_value={"id": "agent-1", "name": "My Agent"})
    app.state.agent_store = agent_store
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/agents/agent-1/keys/manifest", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["name"] == "My Agent"
    agent_store.get_agent.assert_awaited_once()


def test_get_agent_manifest_agent_store_exception_falls_back() -> None:
    app = _make_app()
    agent_store = AsyncMock()
    agent_store.get_agent = AsyncMock(side_effect=RuntimeError("boom"))
    app.state.agent_store = agent_store
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/agents/agent-1/keys/manifest", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["agent_id"] == "agent-1"


def test_get_agent_manifest_agent_store_returns_falsy_dict_falls_back() -> None:
    app = _make_app()
    agent_store = AsyncMock()
    agent_store.get_agent = AsyncMock(return_value=None)
    app.state.agent_store = agent_store
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/agents/agent-1/keys/manifest", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["agent_id"] == "agent-1"
