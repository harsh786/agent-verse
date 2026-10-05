"""MDB-01 / A7 / TG-01: a connection string is a secret, stored once, never returned.

``GET /connectors`` and ``GET /connectors/{id}`` returned
``mongodb://alice:S3cretPw@...`` (top-level ``url`` AND ``auth_config.url``)
and the password sat in plaintext in the Redis connector JSON. Now the URI is
sealed in the connector secret store (one copy: ``auth_config`` holds its
vault reference, ``url`` is ``builtin://``) and every response shows only the
masked host.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import fakeredis.aioredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.connectors import router as connectors_router
from app.mcp.registry import MCPRegistry
from app.providers.vault import is_connector_secret_ref
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

A = TenantContext(tenant_id="tid-dsn-a", plan=PlanTier.PROFESSIONAL, api_key_id="kid-a")
KEYS = {"key-a": A}
H = {"X-API-Key": "key-a"}
URI = "mongodb://alice:S3cretPw@8.8.8.8:27017,8.8.4.4:27017/shop?replicaSet=rs0&authSource=admin"
NEW_URI = "mongodb://bob:N3wSecret@8.8.8.8:27017/shop"
SECRETS = ("S3cretPw", "alice", "N3wSecret", "bob:")


def _app() -> tuple[TestClient, Any, FastAPI]:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return KEYS.get(key)

    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(connectors_router)
    app.state.mcp_registry = MCPRegistry(redis=redis)
    return TestClient(app, raise_server_exceptions=False), redis, app


def _assert_clean(payload: Any) -> None:
    text = json.dumps(payload)
    for secret in SECRETS:
        assert secret not in text, f"{secret!r} leaked in {text}"


async def _redis_dump(redis: Any) -> str:
    out: list[str] = []
    async for key in redis.scan_iter(match="*"):
        kind = await redis.type(key)
        if kind == "string":
            out.append(f"{key}={await redis.get(key)}")
        elif kind == "set":
            out.append(f"{key}={sorted(await redis.smembers(key))}")
    return "\n".join(out)


@pytest.mark.parametrize(
    "payload",
    [
        # The catalog flow: URI in auth_config, url is the builtin marker.
        {"url": "builtin://", "auth_config": {"url": URI}},
        # The URI entered in BOTH places (the old form): one copy is kept.
        {"url": URI, "auth_config": {"url": URI}},
        # Only the top-level url.
        {"url": URI, "auth_config": {}},
        # Other URI keys the handler reads.
        {"url": "builtin://", "auth_config": {"connection_string": URI}},
        {"url": "builtin://", "auth_config": {"uri": URI}},
    ],
)
def test_register_list_get_never_return_userinfo(payload: dict[str, Any]) -> None:
    client, redis, app = _app()
    resp = client.post(
        "/connectors",
        headers=H,
        json={"name": "analytics", "type": "mongodb", "auth_type": "none", **payload},
    )
    assert resp.status_code == 201, resp.text
    _assert_clean(resp.json())
    sid = resp.json()["server_id"]
    assert resp.json()["url"] == "builtin://"

    got = client.get(f"/connectors/{sid}", headers=H).json()
    listed = next(c for c in client.get("/connectors", headers=H).json() if c["server_id"] == sid)
    for row in (got, listed):
        _assert_clean(row)
        assert row["url"] == "builtin://" and row["base_url"] == "builtin://"
        uri_values = [v for v in row["auth_config"].values() if v != "<redacted>"]
        assert not any("mongodb" in str(v) for v in uri_values)
        # The non-secret display form names the hosts (no credentials).
        assert row["display_url"].startswith("mongodb://8.8.8.8:27017,8.8.4.4:27017/shop")
        assert "authSource=admin" in row["display_url"]

    # A7: one source of truth, sealed in the secret store.
    store = app.state.connector_secret_store
    refs = [r for r in store if str(r).startswith(f"vault://connectors/{sid}/")]
    assert len(refs) == 1, store
    assert store[refs[0]] == URI

    # Nothing in Redis holds the password or the username.
    dump = asyncio.run(_redis_dump(redis))
    for secret in SECRETS:
        assert secret not in dump, dump


def test_update_keeps_the_sealed_uri_and_replaces_it_on_change() -> None:
    client, redis, app = _app()
    sid = client.post(
        "/connectors",
        headers=H,
        json={
            "name": "analytics",
            "type": "mongodb",
            "url": "builtin://",
            "auth_type": "none",
            "auth_config": {"url": URI, "database": "shop"},
        },
    ).json()["server_id"]
    row = client.get(f"/connectors/{sid}", headers=H).json()

    # The edit form sends back what it was given (masked values): nothing changes.
    unchanged = client.put(
        f"/connectors/{sid}",
        headers=H,
        json={
            "name": "analytics",
            "url": row["display_url"],
            "auth_type": "none",
            "auth_config": row["auth_config"],
        },
    )
    assert unchanged.status_code == 200, unchanged.text
    _assert_clean(unchanged.json())
    store = app.state.connector_secret_store
    ref = row["auth_config"].get("url")
    assert ref == "<redacted>"
    sealed = [r for r in store if str(r).startswith(f"vault://connectors/{sid}/")]
    assert [store[r] for r in sealed] == [URI]
    assert unchanged.json()["url"] == "builtin://"
    assert unchanged.json()["display_url"] == row["display_url"]

    # A new URI is sealed again; the response never echoes it.
    changed = client.put(
        f"/connectors/{sid}",
        headers=H,
        json={
            "name": "analytics",
            "url": "builtin://",
            "auth_type": "none",
            "auth_config": {"url": NEW_URI, "database": "shop"},
        },
    )
    assert changed.status_code == 200, changed.text
    _assert_clean(changed.json())
    assert [store[r] for r in sealed] == [NEW_URI]
    assert changed.json()["display_url"] == "mongodb://8.8.8.8:27017/shop"
    dump = asyncio.run(_redis_dump(redis))
    for secret in SECRETS:
        assert secret not in dump, dump


def test_stored_config_holds_only_the_vault_reference() -> None:
    client, _redis, app = _app()
    sid = client.post(
        "/connectors",
        headers=H,
        json={
            "name": "analytics",
            "type": "mongodb",
            "url": URI,
            "auth_type": "none",
            "auth_config": {"url": URI},
        },
    ).json()["server_id"]
    cfg = asyncio.run(app.state.mcp_registry.get(sid, tenant_ctx=A))
    assert cfg is not None
    assert cfg.url == "builtin://" and cfg.base_url == "builtin://"
    assert is_connector_secret_ref(cfg.auth_config["url"])
    _assert_clean(cfg.model_dump(mode="json"))


async def test_the_handler_still_receives_the_real_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    """The sealed reference is resolved for the call (the tool works end to end)."""
    from app.mcp.client import MCPClient
    from app.mcp.servers import mongodb_server

    client, _redis, app = _app()
    sid = client.post(
        "/connectors",
        headers=H,
        json={
            "name": "analytics",
            "type": "mongodb",
            "url": "builtin://",
            "auth_type": "none",
            "auth_config": {"url": URI},
        },
    ).json()["server_id"]
    seen: dict[str, Any] = {}

    async def _handler(
        tool: str, args: dict[str, Any], credentials: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        seen.update(credentials or {})
        return {"count": 1}

    MCPRegistry.register_builtin_handler("builtin-mongodb", _handler)
    store = app.state.connector_secret_store

    async def _resolver(ref: str, tenant_ctx: Any = None) -> str | None:
        return store.get(ref)

    mcp = MCPClient(registry=app.state.mcp_registry, secret_resolver=_resolver)
    try:
        result = await mcp.call_tool(
            server_id=sid, tool_name="mongodb_count", arguments={"collection": "c"}, tenant_ctx=A
        )
    finally:
        MCPRegistry.register_builtin_handler("builtin-mongodb", mongodb_server.call_tool)
    assert result.success, result.error
    assert seen["url"] == URI
