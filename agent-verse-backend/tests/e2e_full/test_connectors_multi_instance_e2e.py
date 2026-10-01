"""Full-stack e2e (MULTI-INSTANCE): two MongoDB and two Redis connectors on ONE tenant.

Boots the real app (``create_app(manage_pools=True)`` — Postgres + Redis), then
registers, through ``POST /connectors``, two MongoDB connections (each its own
user + database on a real MongoDB) and two Redis connections (each its own
logical database on a real Redis). Every connection must persist with its own
id, share its built-in type, and a tool call on each must reach ITS backend
with ITS credentials; deleting one leaves the other working.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from app.tenancy.context import PlanTier, TenantContext

pytestmark = [
    pytest.mark.e2e_full,
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.fixture(scope="module")
def backends() -> Iterator[dict[str, Any]]:
    if not os.environ.get("DOCKER_HOST") and not os.path.exists("/var/run/docker.sock"):
        pytest.skip("Docker not available")
    import redis as redis_sync
    from pymongo import MongoClient
    from testcontainers.mongodb import MongoDbContainer
    from testcontainers.redis import RedisContainer

    with (
        MongoDbContainer("mongo:7.0", username="root", password="rootpw") as mongo,
        RedisContainer("redis:7-alpine") as rds,
    ):
        mhost = mongo.get_container_host_ip()
        mport = int(mongo.get_exposed_port(27017))
        admin = MongoClient(mhost, mport, username="root", password="rootpw")
        try:
            for db_name, user, pw in (
                ("orders", "u_orders", "pw-o"),
                ("analytics", "u_an", "pw-a"),
            ):
                db = admin[db_name]
                db.command("createUser", user, pwd=pw, roles=[{"role": "readWrite", "db": db_name}])
                db["things"].insert_one({"backend": db_name})
        finally:
            admin.close()
        rhost = rds.get_container_host_ip()
        rport = int(rds.get_exposed_port(6379))
        for db_index in (1, 2):
            r = redis_sync.Redis(host=rhost, port=rport, db=db_index)
            r.set("which", f"redis-db-{db_index}")
            r.close()
        yield {"mhost": mhost, "mport": mport, "rhost": rhost, "rport": rport}


async def _tenant(client: Any) -> tuple[TenantContext, dict[str, str]]:
    resp = await client.post(
        "/tenants/signup",
        json={"name": "Multi E2E", "email": f"multi-e2e-{uuid.uuid4().hex[:12]}@example.com"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    ctx = TenantContext(
        tenant_id=body["tenant_id"],
        plan=PlanTier(body.get("plan", "free")),
        api_key_id=body.get("api_key_id", "multi-e2e"),
        roles=("admin",),
    )
    return ctx, {"X-API-Key": body["api_key"]}


async def test_two_mongodb_and_two_redis_connections_each_hit_their_backend(
    app: Any, client: Any, backends: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.ingestion.connector_egress as egress

    # The containers listen on the Docker host's loopback: operator allowlist only.
    hosts = sorted({backends["mhost"], backends["rhost"]})
    monkeypatch.setattr(egress, "_effective_allowlist", lambda: hosts)
    ctx, headers = await _tenant(client)
    m, r = backends, backends
    specs = {
        "orders-db": (
            "mongodb",
            f"mongodb://u_orders:pw-o@{m['mhost']}:{m['mport']}/orders?authSource=orders",
        ),
        "analytics-db": (
            "mongodb",
            f"mongodb://u_an:pw-a@{m['mhost']}:{m['mport']}/analytics?authSource=analytics",
        ),
        "cache-one": ("redis", f"redis://{r['rhost']}:{r['rport']}/1"),
        "cache-two": ("redis", f"redis://{r['rhost']}:{r['rport']}/2"),
    }
    ids: dict[str, str] = {}
    for name, (ctype, url) in specs.items():
        resp = await client.post(
            "/connectors",
            json={
                "name": name,
                "type": ctype,
                "url": "builtin://",
                "auth_type": "none",
                "auth_config": {"url": url},
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        ids[name] = resp.json()["server_id"]
    assert len(set(ids.values())) == 4

    listed = {
        row["server_id"]: row for row in (await client.get("/connectors", headers=headers)).json()
    }
    assert {listed[ids[n]]["builtin_type"] for n in ("orders-db", "analytics-db")} == {
        "builtin-mongodb"
    }
    assert {listed[ids[n]]["builtin_type"] for n in ("cache-one", "cache-two")} == {"builtin-redis"}
    dup = await client.post(
        "/connectors",
        json={
            "name": "Orders-DB",
            "type": "mongodb",
            "url": "builtin://",
            "auth_type": "none",
            "auth_config": {"url": specs["orders-db"][1]},
        },
        headers=headers,
    )
    assert dup.status_code == 409

    mcp = app.state.mcp_client

    async def call(name: str, tool: str, args: dict[str, Any]) -> Any:
        result = await mcp.call_tool(
            server_id=ids[name], tool_name=tool, arguments=args, tenant_ctx=ctx
        )
        assert result.success, f"{name}: {result.error}"
        return result.output

    for name, backend in (("orders-db", "orders"), ("analytics-db", "analytics")):
        out = await call(name, "mongodb_find", {"collection": "things"})
        assert [d["backend"] for d in out["documents"]] == [backend]
    assert (await call("cache-one", "redis_get", {"key": "which"}))["value"] == "redis-db-1"
    assert (await call("cache-two", "redis_get", {"key": "which"}))["value"] == "redis-db-2"

    # Each connection's credentials are its own: orders-db cannot read analytics.
    cross = await mcp.call_tool(
        server_id=ids["orders-db"],
        tool_name="mongodb_find",
        arguments={"collection": "things", "database": "analytics"},
        tenant_ctx=ctx,
    )
    assert not cross.success and "not authorized" in (cross.error or "").lower()

    # Deleting one connection leaves its sibling of the same type working.
    assert (
        await client.delete(f"/connectors/{ids['orders-db']}", headers=headers)
    ).status_code == 204
    out = await call("analytics-db", "mongodb_find", {"collection": "things"})
    assert [d["backend"] for d in out["documents"]] == ["analytics"]
