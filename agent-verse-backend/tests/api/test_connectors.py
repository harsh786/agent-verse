"""Tests for /connectors API endpoints."""

from __future__ import annotations

import base64
import builtins
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.connectors import router as connectors_router
from app.mcp.registry import MCPRegistry
from app.providers.vault import RedisConnectorSecretStore, get_vault
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_CTX = TenantContext(tenant_id="tid-test", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
_VALID_KEY = "av_professional_testkey"


class _FakeRedis:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}
        self._s: dict[str, builtins.set[str]] = {}

    async def get(self, k: str) -> str | None:
        return self._d.get(k)

    async def set(self, k: str, v: str, ex: int | None = None) -> None:
        self._d[k] = v

    async def delete(self, k: str) -> int:
        existed = k in self._d
        self._d.pop(k, None)
        return int(existed)

    async def sadd(self, k: str, v: str) -> None:
        self._s.setdefault(k, set()).add(v)

    async def srem(self, k: str, v: str) -> None:
        self._s.get(k, set()).discard(v)

    async def smembers(self, k: str) -> builtins.set[str]:
        return self._s.get(k, set())


def _make_app(fake_registry: Any) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(connectors_router)
    app.state.mcp_registry = fake_registry
    return app


def test_register_connector_returns_201() -> None:
    reg = AsyncMock()
    reg.register.return_value = "srv-abc"
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.post(
        "/connectors",
        json={
            "name": "github",
            "url": "http://localhost:9000",
            "auth_type": "bearer",
            "auth_config": {"token": "ghp_xxx"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 201
    assert resp.json()["server_id"] == "srv-abc"


@pytest.mark.asyncio
async def test_register_connector_stores_secret_refs_not_raw_values() -> None:
    reg = MCPRegistry(redis=_FakeRedis())
    app = _make_app(reg)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/connectors",
        json={
            "name": "github",
            "url": "http://localhost:9000",
            "auth_type": "bearer",
            "auth_config": {"token": "ghp_secret"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 201
    server_id = resp.json()["server_id"]
    cfg = await reg.get(server_id, tenant_ctx=_CTX)
    assert cfg is not None
    assert cfg.auth_config["token"] == f"vault://connectors/{server_id}/token"
    assert "ghp_secret" not in cfg.model_dump_json()
    assert app.state.connector_secret_store[cfg.auth_config["token"]] == "ghp_secret"


@pytest.mark.asyncio
async def test_register_connector_stores_sensitive_custom_header_refs() -> None:
    reg = MCPRegistry(redis=_FakeRedis())
    app = _make_app(reg)
    client = TestClient(app, raise_server_exceptions=False)
    auth_config = {
        "X-API-Key": "api-secret",
        "client_secret": "client-secret",
        "secret": "generic-secret",
        "private_key": "private-key-secret",
        "access_token": "access-token-secret",
        "refresh_token": "refresh-token-secret",
        "X-Trace-ID": "trace-123",
    }

    resp = client.post(
        "/connectors",
        json={
            "name": "custom",
            "url": "https://custom.example.com/mcp",
            "auth_type": "custom_header",
            "auth_config": auth_config,
        },
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 201
    server_id = resp.json()["server_id"]
    cfg = await reg.get(server_id, tenant_ctx=_CTX)
    assert cfg is not None
    sensitive_keys = set(auth_config) - {"X-Trace-ID"}
    for key in sensitive_keys:
        ref = f"vault://connectors/{server_id}/{key}"
        assert cfg.auth_config[key] == ref
        assert app.state.connector_secret_store[ref] == auth_config[key]
        assert auth_config[key] not in cfg.model_dump_json()
    assert cfg.auth_config["X-Trace-ID"] == "trace-123"

    listed = client.get("/connectors", headers={"X-API-Key": _VALID_KEY})

    assert listed.status_code == 200
    public_auth = listed.json()[0]["auth_config"]
    for key in sensitive_keys:
        assert public_auth[key] == "<redacted>"
    assert public_auth["X-Trace-ID"] == "trace-123"


@pytest.mark.asyncio
async def test_register_connector_rejects_in_memory_secret_store_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    reg = MCPRegistry(redis=_FakeRedis())
    client = TestClient(_make_app(reg), raise_server_exceptions=False)

    resp = client.post(
        "/connectors",
        json={
            "name": "github",
            "url": "https://api.github.com/mcp",
            "auth_type": "bearer",
            "auth_config": {"token": "prod-token"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 503
    assert "production connector secret storage" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_register_connector_uses_encrypted_redis_secret_store_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AGENTVERSE_VAULT_KEY", "test-production-vault-key")
    redis = _FakeRedis()
    reg = MCPRegistry(redis=redis)
    app = _make_app(reg)
    app.state.connector_secret_store = RedisConnectorSecretStore(
        redis=redis,
        vault=get_vault(),
    )
    app.state.connector_secret_store_is_production_safe = True
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.post(
        "/connectors",
        json={
            "name": "github",
            "url": "https://api.github.com/mcp",
            "auth_type": "bearer",
            "auth_config": {"token": "prod-token"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 201
    server_id = resp.json()["server_id"]
    ref = f"vault://connectors/{server_id}/token"
    cfg = await reg.get(server_id, tenant_ctx=_CTX)
    assert cfg is not None
    assert cfg.auth_config["token"] == ref
    assert all("prod-token" not in value for value in redis._d.values())
    assert await app.state.connector_secret_store.resolve(ref, tenant_ctx=_CTX) == "prod-token"


def test_update_connector_returns_updated_record() -> None:
    from app.mcp.registry import MCPServerConfig

    reg = AsyncMock()
    reg.get.return_value = MCPServerConfig(
        name="old-jira",
        url="https://mcp.atlassian.com/v1/mcp",
        auth_type="basic",
        auth_config={"username": "old@example.com", "password": "old-token"},
    )
    reg.update.return_value = True
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.put(
        "/connectors/srv-abc",
        json={
            "name": "jira",
            "url": "https://mcp.atlassian.com/v1/mcp",
            "auth_type": "basic",
            "auth_config": {"username": "user@example.com", "password": "token"},
            "description": "Jira MCP",
            "priority": 5,
        },
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["server_id"] == "srv-abc"
    assert body["name"] == "jira"
    reg.update.assert_called_once()


def test_list_connectors_returns_200() -> None:
    reg = AsyncMock()
    reg.list_servers.return_value = []
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.get("/connectors", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_list_connectors_includes_server_id_for_ui_actions() -> None:
    from app.mcp.registry import MCPServerConfig

    reg = AsyncMock()
    reg.list_server_records.return_value = [
        (
            "srv-abc",
            MCPServerConfig(
                name="jira",
                url="http://jira-mcp.local",
                auth_type="api_key",
                auth_config={"header_name": "X-API-Key", "api_key": "secret"},
            ),
        )
    ]
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.get("/connectors", headers={"X-API-Key": _VALID_KEY})

    assert resp.status_code == 200
    assert resp.json()[0]["server_id"] == "srv-abc"


def test_list_connectors_redacts_sensitive_auth_config() -> None:
    from app.mcp.registry import MCPServerConfig

    reg = AsyncMock()
    reg.list_server_records.return_value = [
        (
            "srv-abc",
            MCPServerConfig(
                name="jira",
                url="https://mcp.atlassian.com/v1/mcp",
                auth_type="basic",
                auth_config={"username": "user@example.com", "password": "secret-token"},
            ),
        )
    ]
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.get("/connectors", headers={"X-API-Key": _VALID_KEY})

    assert resp.status_code == 200
    assert resp.json()[0]["auth_config"] == {
        "username": "user@example.com",
        "password": "<redacted>",
    }


@pytest.mark.asyncio
async def test_list_connectors_redacts_secret_refs() -> None:
    reg = MCPRegistry(redis=_FakeRedis())
    app = _make_app(reg)
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post(
        "/connectors",
        json={
            "name": "jira",
            "url": "https://mcp.atlassian.com/v1/mcp",
            "auth_type": "basic",
            "auth_config": {"username": "user@example.com", "password": "secret-token"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert created.status_code == 201

    resp = client.get("/connectors", headers={"X-API-Key": _VALID_KEY})

    assert resp.status_code == 200
    assert resp.json()[0]["auth_config"] == {
        "username": "user@example.com",
        "password": "<redacted>",
    }


def test_update_connector_preserves_redacted_secret_values() -> None:
    from app.mcp.registry import MCPServerConfig

    reg = AsyncMock()
    reg.get.return_value = MCPServerConfig(
        name="jira",
        url="https://mcp.atlassian.com/v1/mcp",
        auth_type="basic",
        auth_config={"username": "old@example.com", "password": "existing-token"},
    )
    reg.update.return_value = True
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.put(
        "/connectors/srv-abc",
        json={
            "name": "jira",
            "url": "https://mcp.atlassian.com/v1/mcp",
            "auth_type": "basic",
            "auth_config": {"username": "new@example.com", "password": "<redacted>"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 200
    updated_cfg = reg.update.call_args.args[1]
    assert updated_cfg.auth_config == {
        "username": "new@example.com",
        "password": "vault://connectors/srv-abc/password",
    }
    assert "existing-token" not in updated_cfg.model_dump_json()


@pytest.mark.asyncio
async def test_update_connector_preserves_existing_secret_ref_when_redacted() -> None:
    reg = MCPRegistry(redis=_FakeRedis())
    app = _make_app(reg)
    client = TestClient(app, raise_server_exceptions=False)
    created = client.post(
        "/connectors",
        json={
            "name": "jira",
            "url": "https://mcp.atlassian.com/v1/mcp",
            "auth_type": "basic",
            "auth_config": {"username": "old@example.com", "password": "existing-token"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )
    assert created.status_code == 201
    server_id = created.json()["server_id"]
    existing_cfg = await reg.get(server_id, tenant_ctx=_CTX)
    assert existing_cfg is not None
    existing_ref = existing_cfg.auth_config["password"]

    resp = client.put(
        f"/connectors/{server_id}",
        json={
            "name": "jira",
            "url": "https://mcp.atlassian.com/v1/mcp",
            "auth_type": "basic",
            "auth_config": {"username": "new@example.com", "password": "<redacted>"},
        },
        headers={"X-API-Key": _VALID_KEY},
    )

    assert resp.status_code == 200
    updated_cfg = await reg.get(server_id, tenant_ctx=_CTX)
    assert updated_cfg is not None
    assert updated_cfg.auth_config == {
        "username": "new@example.com",
        "password": existing_ref,
    }
    assert app.state.connector_secret_store[existing_ref] == "existing-token"


def test_unregister_connector_returns_204() -> None:
    reg = AsyncMock()
    reg.unregister.return_value = True
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.delete("/connectors/srv-abc", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 204


def test_unregister_missing_connector_returns_404() -> None:
    reg = AsyncMock()
    reg.unregister.return_value = False
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.delete("/connectors/ghost", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_get_catalog_returns_all_connectors() -> None:
    reg = AsyncMock()
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.get("/connectors/catalog", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 200
    names = {c["name"] for c in resp.json()}
    assert "github" in names
    assert "slack" in names


def test_register_connector_requires_auth() -> None:
    reg = AsyncMock()
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.post(
        "/connectors",
        json={"name": "x", "url": "http://localhost", "auth_type": "bearer", "auth_config": {}},
    )
    assert resp.status_code == 401


# ── test connector endpoint ────────────────────────────────────────────────────

def test_test_connector_not_found() -> None:
    reg = AsyncMock()
    reg.get.return_value = None
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.post("/connectors/nonexistent/test", headers={"X-API-Key": _VALID_KEY})
    assert resp.status_code == 404


def test_test_connector_reports_auth_failure(monkeypatch: Any) -> None:
    from app.mcp.registry import MCPServerConfig

    class _Response:
        status_code = 401
        text = "invalid token"

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def get(self, *args: Any, **kwargs: Any) -> _Response:
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    reg = AsyncMock()
    reg.get.return_value = MCPServerConfig(
        name="myservice",
        url="https://myservice.example.com",
        auth_type="api_key",
        auth_config={"header_name": "X-API-Key", "api_key": "secret"},
    )
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.post("/connectors/srv-abc/test", headers={"X-API-Key": _VALID_KEY})

    assert resp.status_code == 200
    # 401 < 500, so the generic fallback marks as reachable=True
    assert resp.json()["reachable"] is True
    assert resp.json()["http_status"] == 401


def test_test_connector_sends_registered_basic_auth_header(monkeypatch: Any) -> None:
    from app.mcp.registry import MCPServerConfig

    seen_headers: dict[str, str] = {}

    class _Response:
        status_code = 200
        text = "ok"

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def get(self, *args: Any, **kwargs: Any) -> _Response:
            seen_headers.update(kwargs.get("headers", {}))
            return _Response()

        async def post(self, *args: Any, **kwargs: Any) -> _Response:
            seen_headers.update(kwargs.get("headers", {}))
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    reg = AsyncMock()
    reg.get.return_value = MCPServerConfig(
        name="jira",
        url="https://mcp.atlassian.com/v1/mcp",
        auth_type="basic",
        auth_config={"username": "user@example.com", "password": "token"},
    )
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.post("/connectors/srv-abc/test", headers={"X-API-Key": _VALID_KEY})

    expected = base64.b64encode(b"user@example.com:token").decode()
    assert resp.status_code == 200
    assert seen_headers["Authorization"] == f"Basic {expected}"


def test_test_connector_uses_mcp_initialize_for_mcp_endpoint(monkeypatch: Any) -> None:
    from app.mcp.registry import MCPServerConfig

    seen: dict[str, Any] = {}

    class _Response:
        status_code = 200
        text = "ok"

    class _Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def get(self, url: str, *args: Any, **kwargs: Any) -> _Response:
            seen["url"] = url
            seen["method"] = "GET"
            return _Response()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)

    reg = AsyncMock()
    reg.get.return_value = MCPServerConfig(
        name="myservice",
        url="https://myservice.example.com/api",
        auth_type="basic",
        auth_config={"username": "user@example.com", "password": "token"},
    )
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.post("/connectors/srv-abc/test", headers={"X-API-Key": _VALID_KEY})

    assert resp.status_code == 200
    assert resp.json()["reachable"] is True
    assert seen.get("url") == "https://myservice.example.com/api"
    assert seen.get("method") == "GET"


def test_oauth_start_requires_oauth_auth_type() -> None:
    from app.mcp.registry import MCPServerConfig
    cfg = MCPServerConfig(name="gh", url="http://github-mcp", auth_type="bearer")
    reg = AsyncMock()
    reg.get.return_value = cfg
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.get(
        "/connectors/oauth/start",
        params={"server_id": "server-123"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 400


def test_oauth_callback_returns_503_when_oauth_manager_missing() -> None:
    """OAuth callback must return 503 (not a fake success) when oauth_manager is not configured."""
    reg = AsyncMock()
    client = TestClient(_make_app(reg), raise_server_exceptions=False)
    resp = client.get(
        "/connectors/oauth/callback",
        params={"code": "abc", "state": "xyz", "server_id": "srv-1"},
        headers={"X-API-Key": _VALID_KEY},
    )
    assert resp.status_code == 503
    data = resp.json()
    assert "OAuth flow manager" in data.get("detail", "")
