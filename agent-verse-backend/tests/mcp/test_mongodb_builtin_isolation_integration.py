"""MONGO-CREDS against a real MongoDB: each tenant reaches ONLY its own database.

Two tenants register a MongoDB connector with their own connection string
(each user exists only in its tenant's database, ``authSource`` = that db). The
platform env ``MONGODB_MCP_URL`` points at a third, platform-owned database —
which the old handler used for every tenant. Each tenant must see only its own
documents, be refused on the other tenant's database, and never see platform
data.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote

import fakeredis.aioredis
import pytest

from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.mcp.servers import mongodb_server
from app.tenancy.context import PlanTier, TenantContext

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TENANT_A = TenantContext(tenant_id="tenant-a", plan=PlanTier.PROFESSIONAL, api_key_id="ka")
TENANT_B = TenantContext(tenant_id="tenant-b", plan=PlanTier.PROFESSIONAL, api_key_id="kb")


@pytest.fixture(scope="module")
def mongo() -> Iterator[dict[str, Any]]:
    if not os.environ.get("DOCKER_HOST"):
        pytest.skip("Docker not configured (DOCKER_HOST unset)")
    from pymongo import MongoClient
    from testcontainers.mongodb import MongoDbContainer

    with MongoDbContainer("mongo:7.0", username="root", password="rootpw") as container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(27017))
        admin = MongoClient(host, port, username="root", password="rootpw")
        try:
            for db_name, user, pw, doc in (
                ("db_a", "user_a", "pw-a", {"owner": "tenant-a"}),
                ("db_b", "user_b", "pw-b", {"owner": "tenant-b"}),
                ("platform_db", "platform", "pw-p", {"owner": "platform"}),
            ):
                db = admin[db_name]
                db.command("createUser", user, pwd=pw, roles=[{"role": "readWrite", "db": db_name}])
                db["things"].insert_one(doc)
        finally:
            admin.close()
        yield {"host": host, "port": port}


def _uri(mongo: dict[str, Any], user: str, pw: str, db: str) -> str:
    return (
        f"mongodb://{quote(user)}:{quote(pw)}@{mongo['host']}:{mongo['port']}/{db}?authSource={db}"
    )


@pytest.fixture
def client(mongo: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Iterator[MCPClient]:
    import app.ingestion.connector_egress as egress

    # The container listens on the Docker host's loopback: allow exactly that
    # host through the operator egress allowlist (tenant input can never widen it).
    monkeypatch.setattr(egress, "_effective_allowlist", lambda: [mongo["host"]])
    monkeypatch.setenv("MONGODB_MCP_URL", _uri(mongo, "platform", "pw-p", "platform_db"))
    MCPRegistry.register_builtin_handler("builtin-mongodb", mongodb_server.call_tool)
    registry = MCPRegistry(redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    yield MCPClient(registry=registry)


async def _register(client: MCPClient, tenant: TenantContext, uri: str) -> str:
    return await client._registry.register(
        MCPServerConfig(
            server_id="builtin-mongodb",
            name="MongoDB",
            base_url="builtin://",
            auth_config={"url": uri},
            tool_definitions=mongodb_server.TOOL_DEFINITIONS,
        ),
        tenant_ctx=tenant,
    )


async def _find(
    client: MCPClient, tenant: TenantContext, sid: str, **args: Any
) -> tuple[bool, Any]:
    result = await client.call_tool(
        server_id=sid,
        tool_name="mongodb_find",
        arguments={"collection": "things", **args},
        tenant_ctx=tenant,
    )
    return result.success, (result.output if result.success else result.error)


async def test_each_tenant_hits_its_own_database(client: MCPClient, mongo: dict[str, Any]) -> None:
    sid_a = await _register(client, TENANT_A, _uri(mongo, "user_a", "pw-a", "db_a"))
    sid_b = await _register(client, TENANT_B, _uri(mongo, "user_b", "pw-b", "db_b"))

    ok_a, out_a = await _find(client, TENANT_A, sid_a)
    ok_b, out_b = await _find(client, TENANT_B, sid_b)

    assert ok_a, out_a
    assert ok_b, out_b
    assert [d["owner"] for d in out_a["documents"]] == ["tenant-a"]
    assert [d["owner"] for d in out_b["documents"]] == ["tenant-b"]


async def test_tenant_cannot_read_other_tenant_or_platform_database(
    client: MCPClient, mongo: dict[str, Any]
) -> None:
    sid_a = await _register(client, TENANT_A, _uri(mongo, "user_a", "pw-a", "db_a"))

    ok_b, err_b = await _find(client, TENANT_A, sid_a, database="db_b")
    ok_p, err_p = await _find(client, TENANT_A, sid_a, database="platform_db")

    assert not ok_b and "not authorized" in str(err_b).lower()
    assert not ok_p and "not authorized" in str(err_p).lower()


async def test_connector_without_credentials_never_reaches_platform_db(
    client: MCPClient,
) -> None:
    sid = await client._registry.register(
        MCPServerConfig(server_id="builtin-mongodb", name="MongoDB", base_url="builtin://"),
        tenant_ctx=TENANT_A,
    )

    ok, err = await _find(client, TENANT_A, sid)

    assert not ok
    assert "credentials" in str(err).lower()
