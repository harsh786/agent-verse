"""MULTI-CONNECTION / MULTI-INSTANCE: several connectors of ONE built-in type.

Registering a connector whose name matched a built-in adopted the canonical id
(``builtin-mongodb``) and the registry keys by (tenant, id), so a second MongoDB
connection silently overwrote the first — config and stored secrets — and a
name like ``mongodb-prod`` got no built-in handler at all.

Now each connection gets its own id (``builtin-<type>:<slug>``), the handler is
resolved by ``builtin_type`` (restart-safe), display names are unique per tenant
(409), colliding tool names are qualified per connection, and each call runs
with ITS connection's credentials.
"""

from __future__ import annotations

import json
from typing import Any

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.tool_context import ToolRef
from app.api.connectors import router as connectors_router
from app.mcp import registry as registry_mod
from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry
from app.mcp.tool_naming import qualify_colliding_tools
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

CTX = TenantContext(tenant_id="tid-multi", plan=PlanTier.PROFESSIONAL, api_key_id="kid-1")
KEY = "av_professional_multi"
HDR = {"X-API-Key": KEY}

ORDERS_URI = "mongodb://orders:pw-orders@8.8.8.8:27017/orders"
ANALYTICS_URI = "mongodb://analytics:pw-analytics@8.8.4.4:27017/analytics"


class _Mongo:
    """Records which backend every MongoClient was built for."""

    dsns: list[str] = []

    def __init__(self, dsn: str, **kwargs: Any) -> None:
        _Mongo.dsns.append(dsn)

    def __getitem__(self, name: str) -> Any:
        class _Coll:
            def __init__(self, coll: str) -> None:
                self.name = coll

            def count_documents(self, query: dict[str, Any]) -> int:
                return 1

        class _DB:
            def __getitem__(self, coll: str) -> _Coll:
                return _Coll(coll)

            def list_collection_names(self) -> list[str]:
                return [name]

        return _DB()

    def close(self) -> None:
        pass


class _Redis:
    urls: list[str] = []

    def __init__(self, url: str) -> None:
        self.url = url

    async def get(self, key: str) -> str:
        return f"value-from-{self.url}"

    async def aclose(self) -> None:
        pass

    async def close(self) -> None:
        pass


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    import pymongo
    import redis.asyncio as aioredis

    import app.net.ssrf_guard as guard

    _Mongo.dsns = []
    _Redis.urls = []
    monkeypatch.setattr(pymongo, "MongoClient", _Mongo)

    def _from_url(url: str, **kw: Any) -> _Redis:
        _Redis.urls.append(url)
        return _Redis(url)

    monkeypatch.setattr(aioredis, "from_url", _from_url)
    import app.mcp.servers.egress as egress

    # Tenant calls dial the checked address (BUILTIN-PINNING); record the URL.
    monkeypatch.setattr(egress, "pinned_redis_client", lambda url, ip, **kw: _from_url(url))
    monkeypatch.setattr(guard, "_resolve_host", lambda host: ["93.184.216.34"])
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    reg = MCPRegistry(redis=redis)
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return CTX if key == KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(connectors_router)
    app.state.mcp_registry = reg
    app.state.mcp_client = MCPClient(registry=reg)
    return {"client": TestClient(app, raise_server_exceptions=False), "reg": reg, "redis": redis}


def _create(client: TestClient, name: str, url: str, **extra: Any) -> Any:
    return client.post(
        "/connectors",
        json={"name": name, "url": "builtin://", "auth_type": "none", "auth_config": {"url": url}}
        | extra,
        headers=HDR,
    )


def test_two_connections_of_one_type_both_persist(world: dict[str, Any]) -> None:
    c = world["client"]
    a = _create(c, "orders-db", ORDERS_URI, type="mongodb")
    b = _create(c, "analytics-db", ANALYTICS_URI, type="MongoDB")

    assert a.status_code == b.status_code == 201
    ida, idb = a.json()["server_id"], b.json()["server_id"]
    assert ida != idb
    assert a.json()["builtin_type"] == b.json()["builtin_type"] == "builtin-mongodb"
    listed = {row["server_id"]: row for row in c.get("/connectors", headers=HDR).json()}
    assert listed[ida]["display_name"] == "orders-db"
    assert listed[idb]["display_name"] == "analytics-db"
    assert listed[ida]["builtin_type"] == listed[idb]["builtin_type"] == "builtin-mongodb"
    assert listed[ida]["builtin_type_name"] == "MongoDB"
    assert listed[ida]["auth_config"]["url"] == ORDERS_URI
    assert listed[idb]["auth_config"]["url"] == ANALYTICS_URI


def test_legacy_name_match_no_longer_overwrites(world: dict[str, Any]) -> None:
    c = world["client"]
    a = _create(c, "MongoDB", ORDERS_URI)
    b = _create(c, "MongoDB Analytics", ANALYTICS_URI, type="builtin-mongodb")

    assert a.status_code == b.status_code == 201
    assert a.json()["server_id"] != b.json()["server_id"]
    assert len(c.get("/connectors", headers=HDR).json()) == 2


def test_duplicate_display_name_is_409(world: dict[str, Any]) -> None:
    c = world["client"]
    assert _create(c, "orders-db", ORDERS_URI, type="mongodb").status_code == 201
    dup = _create(c, "  Orders-DB ", ANALYTICS_URI, type="mongodb")

    assert dup.status_code == 409
    assert "already exists" in dup.json()["detail"]
    assert len(c.get("/connectors", headers=HDR).json()) == 1


def test_rename_onto_another_connector_is_409(world: dict[str, Any]) -> None:
    c = world["client"]
    _create(c, "orders-db", ORDERS_URI, type="mongodb")
    idb = _create(c, "analytics-db", ANALYTICS_URI, type="mongodb").json()["server_id"]

    resp = c.put(
        f"/connectors/{idb}",
        json={
            "name": "orders-db",
            "url": "builtin://",
            "auth_type": "none",
            "auth_config": {"url": ANALYTICS_URI},
        },
        headers=HDR,
    )

    assert resp.status_code == 409


def test_type_inferred_from_name_only_when_unambiguous(world: dict[str, Any]) -> None:
    c = world["client"]
    prod = _create(c, "mongodb-prod", ORDERS_URI)
    other = c.post(
        "/connectors",
        json={
            "name": "Acme internal API",
            "url": "https://api.acme.example",
            "auth_type": "none",
            "auth_config": {},
        },
        headers=HDR,
    )

    assert prod.json()["builtin_type"] == "builtin-mongodb"
    assert other.status_code == 201 and other.json()["builtin_type"] == ""


def test_unknown_declared_type_is_422(world: dict[str, Any]) -> None:
    resp = _create(world["client"], "x", ORDERS_URI, type="no-such-connector")
    assert resp.status_code == 422


async def _call(world: dict[str, Any], sid: str, tool: str, args: dict[str, Any]) -> Any:
    return await MCPClient(registry=world["reg"]).call_tool(
        server_id=sid, tool_name=tool, arguments=args, tenant_ctx=CTX
    )


async def test_each_connection_calls_with_its_own_credentials(world: dict[str, Any]) -> None:
    c = world["client"]
    ida = _create(c, "orders-db", ORDERS_URI, type="mongodb").json()["server_id"]
    idb = _create(c, "analytics-db", ANALYTICS_URI, type="mongodb").json()["server_id"]

    ra = await _call(world, ida, "mongodb_count", {"collection": "x"})
    rb = await _call(world, idb, "mongodb_count", {"collection": "x"})

    assert ra.success and rb.success, (ra.error, rb.error)
    assert "8.8.8.8" in _Mongo.dsns[0] and "orders" in _Mongo.dsns[0]
    assert "8.8.4.4" in _Mongo.dsns[1] and "analytics" in _Mongo.dsns[1]


async def test_two_redis_connections_hit_their_own_backend(world: dict[str, Any]) -> None:
    c = world["client"]
    ida = _create(c, "cache-a", "redis://8.8.8.8:6379/1", type="redis").json()["server_id"]
    idb = _create(c, "cache-b", "redis://8.8.4.4:6379/2", type="redis").json()["server_id"]

    ra = await _call(world, ida, "redis_get", {"key": "k"})
    rb = await _call(world, idb, "redis_get", {"key": "k"})

    assert ra.output["value"] == "value-from-redis://8.8.8.8:6379/1"
    assert rb.output["value"] == "value-from-redis://8.8.4.4:6379/2"


async def test_update_and_delete_leave_the_other_connection_untouched(
    world: dict[str, Any],
) -> None:
    c = world["client"]
    ida = _create(c, "orders-db", ORDERS_URI, type="mongodb").json()["server_id"]
    idb = _create(c, "analytics-db", ANALYTICS_URI, type="mongodb").json()["server_id"]

    new_uri = "mongodb://orders:rotated@8.8.8.8:27017/orders"
    upd = c.put(
        f"/connectors/{ida}",
        json={
            "name": "orders-db",
            "url": "builtin://",
            "auth_type": "none",
            "auth_config": {"url": new_uri},
        },
        headers=HDR,
    )
    assert upd.status_code == 200
    assert upd.json()["builtin_type"] == "builtin-mongodb"
    b = await world["reg"].get(idb, tenant_ctx=CTX)
    assert b.auth_config["url"] == ANALYTICS_URI

    assert c.delete(f"/connectors/{ida}", headers=HDR).status_code == 204
    remaining = [row["server_id"] for row in c.get("/connectors", headers=HDR).json()]
    assert remaining == [idb]
    rb = await _call(world, idb, "mongodb_count", {"collection": "x"})
    assert rb.success and "8.8.4.4" in _Mongo.dsns[-1]


async def test_test_connection_uses_the_instance_and_its_type(world: dict[str, Any]) -> None:
    c = world["client"]
    _create(c, "orders-db", ORDERS_URI, type="mongodb")
    idb = _create(c, "analytics-db", ANALYTICS_URI, type="mongodb").json()["server_id"]

    resp = c.post(f"/connectors/{idb}/test", headers=HDR)

    assert resp.json()["status"] == "passed", resp.json()
    assert len(_Mongo.dsns) == 1 and "8.8.4.4" in _Mongo.dsns[0]


async def test_handlers_survive_a_restart(
    world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    c = world["client"]
    ida = _create(c, "orders-db", ORDERS_URI, type="mongodb").json()["server_id"]
    idb = _create(c, "analytics-db", ANALYTICS_URI, type="mongodb").json()["server_id"]
    # A new process: empty handler registry, a fresh registry over the same Redis.
    monkeypatch.setattr(registry_mod, "_BUILTIN_HANDLER_REGISTRY", {})
    fresh = MCPRegistry(redis=world["redis"])

    for sid, host in ((ida, "8.8.8.8"), (idb, "8.8.4.4")):
        result = await MCPClient(registry=fresh).call_tool(
            server_id=sid,
            tool_name="mongodb_count",
            arguments={"collection": "x"},
            tenant_ctx=CTX,
        )
        assert result.success, result.error
        assert host in _Mongo.dsns[-1]


async def test_legacy_canonical_id_still_works(world: dict[str, Any]) -> None:
    # A row written before builtin_type existed, under the canonical id.
    legacy = {
        "server_id": "builtin-mongodb",
        "name": "MongoDB",
        "url": "builtin://",
        "auth_config": {"url": ORDERS_URI},
    }
    await world["redis"].set("mcp:servers:tid-multi:builtin-mongodb", json.dumps(legacy))
    await world["redis"].sadd("mcp:server_ids:tid-multi", "builtin-mongodb")

    cfg = await world["reg"].get("builtin-mongodb", tenant_ctx=CTX)
    result = await _call(world, "builtin-mongodb", "mongodb_count", {"collection": "x"})

    assert cfg.builtin_type == "builtin-mongodb"
    assert result.success, result.error
    assert "8.8.8.8" in _Mongo.dsns[-1]


# ── tool naming across connections ───────────────────────────────────────────


def test_colliding_tools_are_qualified_per_connection() -> None:
    def ref(sid: str, server: str, name: str) -> ToolRef:
        return ToolRef(
            server_id=sid, server_name=server, name=name, description="", input_schema={}
        )

    tools = qualify_colliding_tools(
        [
            ref("builtin-mongodb:orders-db", "orders-db", "mongodb_find"),
            ref("builtin-mongodb:analytics-db", "analytics-db", "mongodb_find"),
            ref("builtin-redis:cache", "cache", "redis_get"),
        ]
    )

    assert [t.name for t in tools] == [
        "orders_db__mongodb_find",
        "analytics_db__mongodb_find",
        "redis_get",
    ]


async def test_qualified_names_route_to_their_connection(world: dict[str, Any]) -> None:
    c = world["client"]
    _create(c, "orders-db", ORDERS_URI, type="mongodb")
    idb = _create(c, "analytics-db", ANALYTICS_URI, type="mongodb").json()["server_id"]
    client = MCPClient(registry=world["reg"])

    direct = await client.call_tool(
        server_id=idb,
        tool_name="analytics_db__mongodb_count",
        arguments={"collection": "x"},
        tenant_ctx=CTX,
    )
    by_dotted = await client.call_tool_by_name(
        tool_name="analytics-db.mongodb_count", arguments={"collection": "x"}, tenant_ctx=CTX
    )
    by_slug = await client.call_tool_by_name(
        tool_name="orders_db__mongodb_count", arguments={"collection": "x"}, tenant_ctx=CTX
    )
    ambiguous = await client.call_tool_by_name(
        tool_name="mongodb_count", arguments={"collection": "x"}, tenant_ctx=CTX
    )

    assert direct.success and by_dotted.success and by_slug.success
    assert ["8.8.4.4" in d for d in _Mongo.dsns] == [True, True, False]
    assert not ambiguous.success and "several connectors" in (ambiguous.error or "")


# ── the UI's field names ─────────────────────────────────────────────────────


def test_ui_connector_type_field_binds_the_builtin(world: dict[str, Any]) -> None:
    """The connectors UI sends and reads ``connector_type`` (the catalog type key)."""
    c = world["client"]
    created = _create(c, "orders-db", ORDERS_URI, connector_type="mongodb")
    sheets = _create(
        c, "finance-sheet", "https://sheets.googleapis.com/v4", connector_type="google_sheets"
    )

    assert created.status_code == 201
    assert created.json()["builtin_type"] == "builtin-mongodb"
    listed = {row["server_id"]: row for row in c.get("/connectors", headers=HDR).json()}
    assert listed[created.json()["server_id"]]["connector_type"] == "mongodb"
    assert listed[sheets.json()["server_id"]]["connector_type"] == "google_sheets"


def test_ui_edit_with_connector_type_keeps_the_type(world: dict[str, Any]) -> None:
    c = world["client"]
    sid = _create(c, "orders-db", ORDERS_URI, connector_type="mongodb").json()["server_id"]

    resp = c.put(
        f"/connectors/{sid}",
        json={
            "name": "orders-db",
            "connector_type": "mongodb",
            "url": "builtin://",
            "auth_type": "none",
            "auth_config": {"url": ORDERS_URI},
        },
        headers=HDR,
    )

    assert resp.status_code == 200
    assert resp.json()["builtin_type"] == "builtin-mongodb"
    assert resp.json()["connector_type"] == "mongodb"
