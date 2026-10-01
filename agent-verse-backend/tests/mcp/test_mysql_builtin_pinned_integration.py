"""BUILTIN-DRIVERS: the MySQL built-in against a real MySQL, through the pinned path.

aiomysql was not installed, so the MySQL built-in returned ``dependency_missing``
in every deployment. Two tenants register a MySQL connection (each its own user
+ database); each call goes through MCPClient (tenant-scoped dispatch), the
connection's host is checked and dialled by its pinned address, and each tenant
sees only its own database.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import fakeredis.aioredis
import pytest

from app.mcp.client import MCPClient
from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.mcp.servers.registry_wiring import get_builtin_server_configs
from app.tenancy.context import PlanTier, TenantContext

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TENANT_A = TenantContext(tenant_id="my-a", plan=PlanTier.PROFESSIONAL, api_key_id="ka")
TENANT_B = TenantContext(tenant_id="my-b", plan=PlanTier.PROFESSIONAL, api_key_id="kb")


@pytest.fixture(scope="module")
def mysql() -> Iterator[dict[str, Any]]:
    if not os.environ.get("DOCKER_HOST"):
        pytest.skip("Docker not configured (DOCKER_HOST unset)")
    import pymysql
    from testcontainers.mysql import MySqlContainer

    with MySqlContainer("mysql:8.0", root_password="rootpw") as container:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(3306))
        conn = pymysql.connect(host=host, port=port, user="root", password="rootpw")
        try:
            with conn.cursor() as cur:
                for db, user, pw in (("db_a", "user_a", "pw-a"), ("db_b", "user_b", "pw-b")):
                    cur.execute(f"CREATE DATABASE {db}")
                    cur.execute(f"CREATE TABLE {db}.things (owner VARCHAR(32))")
                    cur.execute(f"INSERT INTO {db}.things VALUES ('{db}')")
                    cur.execute(f"CREATE USER '{user}'@'%' IDENTIFIED BY '{pw}'")
                    cur.execute(f"GRANT SELECT ON {db}.* TO '{user}'@'%'")
            conn.commit()
        finally:
            conn.close()
        yield {"host": host, "port": port}


@pytest.fixture
async def client(mysql: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> MCPClient:
    import app.ingestion.connector_egress as egress

    # The container listens on the Docker host's loopback: operator allowlist only.
    monkeypatch.setattr(egress, "_effective_allowlist", lambda: [mysql["host"]])
    monkeypatch.setenv("MYSQL_MCP_URL", "mysql://root:rootpw@127.0.0.1:1/platform")
    (spec,) = [c for c in get_builtin_server_configs() if c["server_id"] == "builtin-mysql"]
    MCPRegistry.register_builtin_handler("builtin-mysql", spec["handler"])
    reg = MCPRegistry(redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    for tenant, user, pw, db in (
        (TENANT_A, "user_a", "pw-a", "db_a"),
        (TENANT_B, "user_b", "pw-b", "db_b"),
    ):
        await reg.register(
            MCPServerConfig(
                server_id=f"builtin-mysql:{db}",
                name=db,
                base_url="builtin://",
                auth_config={"url": f"mysql://{user}:{pw}@{mysql['host']}:{mysql['port']}/{db}"},
                builtin_type="builtin-mysql",
                tool_definitions=spec["tool_definitions"],
            ),
            tenant_ctx=tenant,
        )
    return MCPClient(registry=reg)


async def _query(client: MCPClient, tenant: TenantContext, sid: str, sql: str) -> Any:
    return await client.call_tool(
        server_id=sid, tool_name="mysql_query", arguments={"sql": sql}, tenant_ctx=tenant
    )


async def test_each_tenant_reads_its_own_mysql_database(
    client: MCPClient, mysql: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import aiomysql

    dialled: list[str] = []
    real_connect = aiomysql.connect

    def _record(**kwargs: Any) -> Any:
        dialled.append(kwargs["host"])
        return real_connect(**kwargs)

    monkeypatch.setattr(aiomysql, "connect", _record)

    a = await _query(client, TENANT_A, "builtin-mysql:db_a", "SELECT owner FROM things")
    b = await _query(client, TENANT_B, "builtin-mysql:db_b", "SELECT owner FROM things")

    assert a.success and b.success, (a.error, b.error)
    assert a.output["rows"] == [{"owner": "db_a"}]
    assert b.output["rows"] == [{"owner": "db_b"}]
    # Dialled by a checked ADDRESS, never by re-resolving the name.
    import ipaddress

    assert dialled
    for host in dialled:
        ipaddress.ip_address(host)


async def test_tenant_cannot_read_the_other_database(client: MCPClient) -> None:
    result = await _query(client, TENANT_A, "builtin-mysql:db_a", "SELECT owner FROM db_b.things")

    assert not result.success
    assert "denied" in (result.error or "").lower()
