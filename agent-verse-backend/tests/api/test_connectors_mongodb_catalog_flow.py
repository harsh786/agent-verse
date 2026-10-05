"""A1 / A3 / A10 / TG-11: the MongoDB connector from the REAL catalog payload.

The frontend tests faked the catalog entry (auth_type 'api_key', has_builtin
true), which hid that the real entry could not be registered: auth_type
'connection_string' was a 422 (A1) and the entry had no builtin_server_id, so
a renamed connection ('orders-db') became a remote MCP server (A3). These tests
drive the API exactly as the UI does, from GET /connectors/catalog.
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.connectors import router as connectors_router
from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

A = TenantContext(tenant_id="tid-cat-a", plan=PlanTier.PROFESSIONAL, api_key_id="ka")
B = TenantContext(tenant_id="tid-cat-b", plan=PlanTier.PROFESSIONAL, api_key_id="kb")
HA = {"X-API-Key": "ka"}
HB = {"X-API-Key": "kb"}
URI = "mongodb://alice:S3cretPw@8.8.8.8:27017/shop"


class _Coll:
    def count_documents(self, query: dict[str, Any]) -> int:
        return 4


class _DB:
    def command(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": 1.0}

    def __getitem__(self, name: str) -> _Coll:
        return _Coll()

    def list_collection_names(self) -> list[str]:
        return ["orders"]


class _Mongo:
    dsns: list[str] = []

    def __init__(self, dsn: str, **kwargs: Any) -> None:
        _Mongo.dsns.append(dsn)

    def __getitem__(self, name: str) -> _DB:
        return _DB()

    def close(self) -> None:
        pass


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import pymongo

    _Mongo.dsns = []
    monkeypatch.setattr(pymongo, "MongoClient", _Mongo)
    app = FastAPI()
    keys = {"ka": A, "kb": B}

    async def _resolve(key: str) -> TenantContext | None:
        return keys.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(connectors_router)
    reg = MCPRegistry(redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    app.state.mcp_registry = reg
    secrets: dict[str, str] = {}
    app.state.connector_secret_store = secrets

    async def _secret(ref: str, tenant_ctx: Any = None) -> str | None:
        return secrets.get(ref)

    app.state.mcp_client = MCPClient(registry=reg, secret_resolver=_secret)
    return {"client": TestClient(app, raise_server_exceptions=False), "app": app}


def _catalog_entry(client: TestClient) -> dict[str, Any]:
    entries = client.get("/connectors/catalog", headers=HA).json()
    return next(e for e in entries if e["name"] == "mongodb")


def _register_like_the_ui(client: TestClient, name: str, **auth: Any) -> Any:
    """ConnectorsCatalogPage: type only when has_builtin; url = default_url."""
    spec = _catalog_entry(client)
    body: dict[str, Any] = {
        "name": name,
        "url": spec["default_url"],
        "auth_type": spec["auth_type"],
        "auth_config": {"url": URI, **auth},
    }
    if spec["has_builtin"]:
        body["type"] = spec["connector_type"]
    return client.post("/connectors", headers=HA, json=body)


def test_catalog_entry_is_a_builtin_connection_string(world: dict[str, Any]) -> None:
    spec = _catalog_entry(world["client"])
    assert spec["auth_type"] == "connection_string"
    assert spec["has_builtin"] is True
    assert spec["builtin_server_id"] == "builtin-mongodb"


@pytest.mark.parametrize("name", ["mongodb", "MongoDB", "orders-db", "analytics"])
def test_register_from_the_real_catalog_payload(world: dict[str, Any], name: str) -> None:
    resp = _register_like_the_ui(world["client"], name)
    assert resp.status_code == 201, resp.text
    assert resp.json()["builtin_type"] == "builtin-mongodb"
    row = world["client"].get(f"/connectors/{resp.json()['server_id']}", headers=HA).json()
    assert row["auth_type"] == "connection_string"
    assert row["has_builtin"] is True and row["connector_type"] == "mongodb"


async def test_the_registered_connection_runs_tools(world: dict[str, Any]) -> None:
    sid = _register_like_the_ui(world["client"], "orders-db").json()["server_id"]
    result = await world["app"].state.mcp_client.call_tool(
        server_id=sid, tool_name="mongodb_count", arguments={"collection": "o"}, tenant_ctx=A
    )
    assert result.success, result.error
    assert result.output["count"] == 4
    assert "8.8.8.8" in _Mongo.dsns[-1]


def test_a_mongodb_uri_without_a_type_is_the_mongodb_builtin(world: dict[str, Any]) -> None:
    """A row with no declared type and no recognisable name: the DSN scheme decides."""
    resp = world["client"].post(
        "/connectors",
        headers=HA,
        json={"name": "orders-db", "url": URI, "auth_type": "none", "auth_config": {"url": URI}},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["builtin_type"] == "builtin-mongodb"


def test_a_legacy_remote_row_with_a_mongodb_uri_is_the_builtin() -> None:
    from app.mcp.registry import MCPServerConfig

    legacy = MCPServerConfig.model_validate(
        {"server_id": "3f2a", "name": "orders-db", "url": URI, "auth_config": {"url": URI}}
    )
    assert legacy.builtin_type == "builtin-mongodb"


def test_tenant_b_cannot_reach_tenant_a_connection(
    world: dict[str, Any],
) -> None:
    """TG-11: another tenant's MongoDB server_id is a 404 / 'not found' everywhere."""
    import asyncio

    client = world["client"]
    sid = _register_like_the_ui(client, "orders-db").json()["server_id"]

    assert client.post(f"/connectors/{sid}/test", headers=HB).status_code == 404
    assert client.get(f"/connectors/{sid}/tools", headers=HB).status_code == 404
    assert client.get(f"/connectors/{sid}", headers=HB).status_code == 404
    assert (
        client.put(
            f"/connectors/{sid}",
            headers=HB,
            json={"name": "x", "url": "builtin://", "auth_type": "none", "auth_config": {}},
        ).status_code
        == 404
    )
    assert client.delete(f"/connectors/{sid}", headers=HB).status_code == 404
    result = asyncio.run(
        world["app"].state.mcp_client.call_tool(
            server_id=sid, tool_name="mongodb_count", arguments={"collection": "o"}, tenant_ctx=B
        )
    )
    assert not result.success
    assert "not found" in (result.error or "").lower()
    assert _Mongo.dsns == []  # tenant B's call never reached tenant A's database
