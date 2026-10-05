"""C2: MongoDB MCP clients are pooled per tenant + connector, bounded, and closed.

Every tool call used to build a MongoClient (DNS check, TCP, TLS, SCRAM, monitor
threads) and close it again. Clients are now cached per (tenant, connector,
credential fingerprint) with an idle TTL, a maximum age and an LRU bound; a
connector update or delete closes its clients; a connection failure evicts.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import get_settings
from app.mcp import mongodb_clients
from app.mcp.servers import mongodb_server
from app.tenancy.context import PlanTier, TenantContext

URI_A = "mongodb://u:p@8.8.8.8:27017/db"
URI_B = "mongodb://u:p@8.8.4.4:27017/db"
T1 = TenantContext(tenant_id="t-1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
T2 = TenantContext(tenant_id="t-2", plan=PlanTier.PROFESSIONAL, api_key_id="k2")


class _Coll:
    def count_documents(self, query: dict[str, Any]) -> int:
        if _Client.fail_next:
            _Client.fail_next = False
            from pymongo.errors import AutoReconnect

            raise AutoReconnect("connection reset")
        return 3


class _DB:
    def command(self, *_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": 1.0}  # the first-contact ping

    def __getitem__(self, name: str) -> _Coll:
        return _Coll()


class _Client:
    built: list[_Client] = []
    fail_next = False

    def __init__(self, dsn: str, **kwargs: Any) -> None:
        self.dsn = dsn
        self.kwargs = kwargs
        self.closed = False
        _Client.built.append(self)

    def __getitem__(self, name: str) -> _DB:
        assert not self.closed, "a closed client was used"
        return _DB()

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> type[_Client]:
    import pymongo

    _Client.built = []
    _Client.fail_next = False
    monkeypatch.setattr(pymongo, "MongoClient", _Client)
    monkeypatch.setenv("MONGODB_CLIENT_CACHE_SIZE", "3")
    monkeypatch.setenv("MONGODB_CLIENT_IDLE_TTL_S", "60")
    monkeypatch.setenv("MONGODB_CLIENT_MAX_AGE_S", "600")
    get_settings.cache_clear()
    mongodb_clients.close_all()
    yield _Client
    mongodb_clients.close_all()
    get_settings.cache_clear()


async def _count(
    uri: str, tenant: TenantContext | None = T1, server_id: str = "builtin-mongodb:a", **extra: Any
) -> dict[str, Any]:
    return await mongodb_server.call_tool(
        "mongodb_count",
        {"collection": "c"},
        credentials={"url": uri, **extra},
        tenant_ctx=tenant,
        server_id=server_id,
    )


async def test_repeated_calls_reuse_one_client(fake: type[_Client]) -> None:
    for _ in range(5):
        assert (await _count(URI_A))["count"] == 3
    assert len(fake.built) == 1
    assert not fake.built[0].closed


async def test_clients_are_per_tenant_and_per_connector(fake: type[_Client]) -> None:
    await _count(URI_A, T1, "builtin-mongodb:a")
    await _count(URI_A, T2, "builtin-mongodb:a")  # same URI, another tenant
    await _count(URI_A, T1, "builtin-mongodb:b")  # same URI, another connector
    assert len(fake.built) == 3


async def test_changed_credentials_build_a_new_client_and_close_the_old(
    fake: type[_Client],
) -> None:
    await _count(URI_A)
    await _count(URI_B)  # the connector's URI was updated
    first, second = fake.built
    assert first.closed and not second.closed
    assert "8.8.4.4" in second.dsn


async def test_the_cache_is_bounded_lru(fake: type[_Client]) -> None:
    for i in range(3):
        await _count(URI_A, T1, f"builtin-mongodb:c{i}")
    await _count(URI_A, T1, "builtin-mongodb:c0")  # c0 is now most recent
    await _count(URI_A, T1, "builtin-mongodb:c3")  # evicts the LRU: c1
    closed = [c for c in fake.built if c.closed]
    assert len(closed) == 1 and closed[0] is fake.built[1]
    assert mongodb_clients.size() == 3


async def test_idle_and_aged_clients_are_closed(
    fake: type[_Client], monkeypatch: pytest.MonkeyPatch
) -> None:
    now = [1000.0]
    monkeypatch.setattr(mongodb_clients, "_now", lambda: now[0])
    await _count(URI_A)
    now[0] += 61  # idle past the TTL
    await _count(URI_A)
    assert len(fake.built) == 2 and fake.built[0].closed
    for _ in range(12):  # used every 59 s: never idle, but older than max age
        now[0] += 59
        await _count(URI_A)
    assert len(fake.built) == 3 and fake.built[1].closed


async def test_connection_failure_evicts_the_client(fake: type[_Client]) -> None:
    await _count(URI_A)
    fake.fail_next = True
    failed = await _count(URI_A)
    assert "error" in failed
    assert fake.built[0].closed
    assert (await _count(URI_A))["count"] == 3
    assert len(fake.built) == 2


async def test_evict_closes_one_connectors_clients_only(fake: type[_Client]) -> None:
    await _count(URI_A, T1, "builtin-mongodb:a")
    await _count(URI_A, T1, "builtin-mongodb:b")
    await _count(URI_A, T2, "builtin-mongodb:a")
    assert mongodb_clients.evict("t-1", "builtin-mongodb:a") == 1
    assert [c.closed for c in fake.built] == [True, False, False]


async def test_an_in_use_client_is_closed_only_after_its_call(fake: type[_Client]) -> None:
    key = ("t-1", "x", "fp")
    entry = mongodb_clients.insert(key, _Client("mongodb://h/db"), lambda: None)
    mongodb_clients.release(entry)  # the inserting call finished
    held = mongodb_clients.acquire(key)
    assert held is entry
    mongodb_clients.evict("t-1", "x")
    assert not entry.client.closed  # still running a call
    mongodb_clients.release(held)
    assert entry.client.closed


async def test_egress_pins_are_held_for_the_client_lifetime(
    fake: type[_Client], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.ingestion.connector_egress as egress

    released: list[str] = []
    real = egress.hold_source_dsn_pins

    def _hold(dsn: str, *, context: str) -> Any:
        pins, release = real(dsn, context=context)

        def _release() -> None:
            released.append(dsn)
            release()

        return pins, _release

    monkeypatch.setattr(egress, "hold_source_dsn_pins", _hold)
    await _count(URI_A)
    await _count(URI_A)
    assert released == []  # the cached client keeps its pins
    mongodb_clients.evict("t-1", "builtin-mongodb:a")
    assert released == [URI_A]


async def test_connector_update_and_delete_close_its_pooled_client(fake: type[_Client]) -> None:
    import fakeredis.aioredis
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.connectors import router as connectors_router
    from app.mcp.client import MCPClient
    from app.mcp.registry import MCPRegistry
    from app.tenancy.middleware import TenantMiddleware

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return T1 if key == "k" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(connectors_router)
    reg = MCPRegistry(redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    app.state.mcp_registry = reg
    secrets: dict[str, str] = {}
    app.state.connector_secret_store = secrets

    async def _secret(ref: str, tenant_ctx: Any = None) -> str | None:
        return secrets.get(ref)

    mcp = MCPClient(registry=reg, secret_resolver=_secret)
    http = TestClient(app)
    body = {"name": "orders-db", "type": "mongodb", "url": "builtin://", "auth_type": "none"}
    sid = http.post(
        "/connectors", headers={"X-API-Key": "k"}, json={**body, "auth_config": {"url": URI_A}}
    ).json()["server_id"]

    async def _call() -> Any:
        return await mcp.call_tool(
            server_id=sid, tool_name="mongodb_count", arguments={"collection": "c"}, tenant_ctx=T1
        )

    assert (await _call()).success
    assert (
        http.put(
            f"/connectors/{sid}",
            headers={"X-API-Key": "k"},
            json={**body, "auth_config": {"url": URI_B}},
        ).status_code
        == 200
    )
    assert fake.built[0].closed  # update closed the client of the old URI
    assert (await _call()).success
    assert "8.8.4.4" in fake.built[1].dsn
    assert http.delete(f"/connectors/{sid}", headers={"X-API-Key": "k"}).status_code == 204
    assert fake.built[1].closed
    assert mongodb_clients.size() == 0
