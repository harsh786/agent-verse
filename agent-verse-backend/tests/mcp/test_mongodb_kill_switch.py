"""NF-13: the MCP built-in MongoDB connector has an operator kill switch.

``MCP_CONNECTOR_MONGODB_ENABLED=false`` refuses new / edited MongoDB connections,
the connector test and every tool call — before any driver client exists.
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.core.config as config_mod
from app.api.connectors import router as connectors_router
from app.mcp.builtin_kill_switch import (
    BuiltinConnectorDisabledError,
    assert_builtin_enabled,
    disabled_reason,
)
from app.mcp.registry import MCPRegistry
from app.mcp.servers import mongodb_server
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

CTX = TenantContext(tenant_id="tid-nf13", plan=PlanTier.PROFESSIONAL, api_key_id="k")
URI = "mongodb://u:p@8.8.8.8:27017/shop"


def _switch(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    settings = config_mod.get_settings().model_copy(
        update={"mcp_connector_mongodb_enabled": enabled}
    )
    monkeypatch.setattr(config_mod, "get_settings", lambda: settings)


@pytest.fixture
def no_driver(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    import pymongo

    built: list[Any] = []

    def _client(*a: Any, **k: Any) -> Any:
        built.append((a, k))
        raise AssertionError("a MongoDB client was built while the connector is disabled")

    monkeypatch.setattr(pymongo, "MongoClient", _client)
    return built


def test_setting_defaults_to_enabled() -> None:
    assert config_mod.Settings.model_fields["mcp_connector_mongodb_enabled"].default is True


def test_reason_names_the_flag_for_type_and_connection_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _switch(monkeypatch, False)
    for builtin in ("builtin-mongodb", "builtin-mongodb:orders-db-1a2b3c"):
        reason = disabled_reason(builtin)
        assert reason is not None and "MCP_CONNECTOR_MONGODB_ENABLED=false" in reason
    assert disabled_reason("builtin-mysql") is None
    with pytest.raises(BuiltinConnectorDisabledError):
        assert_builtin_enabled("builtin-mongodb")
    _switch(monkeypatch, True)
    assert disabled_reason("builtin-mongodb") is None


def test_unreadable_settings_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("settings broken")

    monkeypatch.setattr(config_mod, "get_settings", _boom)
    assert disabled_reason("builtin-mongodb") is not None


@pytest.mark.asyncio
async def test_tool_call_is_refused_before_any_client(
    monkeypatch: pytest.MonkeyPatch, no_driver: list[Any]
) -> None:
    _switch(monkeypatch, False)
    out = await mongodb_server.call_tool(
        "mongodb_find",
        {"collection": "orders", "filter": {}},
        credentials={"url": URI},
        tenant_ctx=CTX,
        server_id="builtin-mongodb:orders",
    )
    assert out["status"] == "connector_disabled"
    assert "MCP_CONNECTOR_MONGODB_ENABLED" in out["error"]
    assert no_driver == []


def _client() -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return CTX if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(connectors_router)
    app.state.mcp_registry = MCPRegistry(redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    app.state.connector_secret_store = {}
    return TestClient(app, raise_server_exceptions=False)


_BODY = {
    "name": "orders-db",
    "type": "mongodb",
    "url": "builtin://",
    "auth_type": "connection_string",
    "auth_config": {"url": URI},
}
_H = {"X-API-Key": "k"}


def test_register_update_and_test_are_refused_when_disabled(
    monkeypatch: pytest.MonkeyPatch, no_driver: list[Any]
) -> None:
    client = _client()
    _switch(monkeypatch, True)
    created = client.post("/connectors", headers=_H, json=_BODY)
    assert created.status_code == 201, created.text
    sid = created.json()["server_id"]

    _switch(monkeypatch, False)
    refused = client.post("/connectors", headers=_H, json={**_BODY, "name": "second-db"})
    assert refused.status_code == 422
    assert "MCP_CONNECTOR_MONGODB_ENABLED" in refused.json()["detail"]

    # A bare mongodb:// connection string is the MongoDB built-in too (A3).
    inferred = client.post(
        "/connectors",
        headers=_H,
        json={"name": "third", "url": URI, "auth_type": "connection_string", "auth_config": {}},
    )
    assert inferred.status_code == 422

    edited = client.put(f"/connectors/{sid}", headers=_H, json=_BODY)
    assert edited.status_code == 422

    tested = client.post(f"/connectors/{sid}/test", headers=_H)
    assert tested.status_code == 200
    assert tested.json()["status"] == "failed"
    assert tested.json()["reason"] == "connector_disabled"
    assert no_driver == []


def test_other_builtins_are_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    _switch(monkeypatch, False)
    resp = _client().post(
        "/connectors",
        headers=_H,
        json={"name": "my-mysql", "type": "mysql", "url": "builtin://",
              "auth_type": "connection_string",
              "auth_config": {"url": "mysql://u:p@8.8.8.8:3306/db"}},
    )
    assert resp.status_code != 422 or "MCP_CONNECTOR_MONGODB" not in resp.text
