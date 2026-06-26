"""Integration tests using real Redis via testcontainers."""
from __future__ import annotations

import pytest

from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def redis_container():
    """Spin up a real Redis container shared across the module."""
    from testcontainers.redis import RedisContainer

    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture
async def redis_client(redis_container):
    """Fresh async Redis client per test — avoids event-loop lifecycle issues."""
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        redis_container.get_connection_url(),
        decode_responses=True,
    )
    yield client
    await client.aclose()


TENANT = TenantContext(
    tenant_id="integ-t1", plan=PlanTier.PROFESSIONAL, api_key_id="integ-k1"
)
TENANT_B = TenantContext(
    tenant_id="integ-t2", plan=PlanTier.FREE, api_key_id="integ-k2"
)


async def test_mcp_registry_real_redis_isolation(redis_client):
    """MCPRegistry with real Redis enforces tenant isolation."""
    registry = MCPRegistry(redis=redis_client)
    cfg_a = MCPServerConfig(name="github-a", url="http://gh-a", auth_type="bearer")
    cfg_b = MCPServerConfig(name="github-b", url="http://gh-b", auth_type="bearer")
    sid_a = await registry.register(cfg_a, tenant_ctx=TENANT)
    sid_b = await registry.register(cfg_b, tenant_ctx=TENANT_B)
    # A can't see B's server
    result = await registry.get(sid_b, tenant_ctx=TENANT)
    assert result is None
    # B can't see A's server
    result2 = await registry.get(sid_a, tenant_ctx=TENANT_B)
    assert result2 is None
    # Each tenant sees only their own
    a_servers = await registry.list_servers(tenant_ctx=TENANT)
    b_servers = await registry.list_servers(tenant_ctx=TENANT_B)
    assert len(a_servers) >= 1
    assert all(s.name == "github-a" for s in a_servers)
    assert all(s.name == "github-b" for s in b_servers)


async def test_mcp_registry_real_redis_unregister(redis_client):
    """Unregistering from real Redis removes the key."""
    registry = MCPRegistry(redis=redis_client)
    cfg = MCPServerConfig(
        name="temp-server", url="http://temp", auth_type="api_key"
    )
    sid = await registry.register(cfg, tenant_ctx=TENANT)
    assert await registry.get(sid, tenant_ctx=TENANT) is not None
    removed = await registry.unregister(sid, tenant_ctx=TENANT)
    assert removed is True
    assert await registry.get(sid, tenant_ctx=TENANT) is None


async def test_mcp_registry_real_redis_list_after_multiple_registers(redis_client):
    """Multiple registrations all appear in list_servers."""
    registry = MCPRegistry(redis=redis_client)
    T3 = TenantContext(
        tenant_id="integ-t3", plan=PlanTier.ENTERPRISE, api_key_id="integ-k3"
    )
    servers = [
        MCPServerConfig(
            name=f"server-{i}", url=f"http://s{i}", auth_type="bearer"
        )
        for i in range(3)
    ]
    for s in servers:
        await registry.register(s, tenant_ctx=T3)

    listed = await registry.list_servers(tenant_ctx=T3)
    listed_names = {s.name for s in listed}
    assert all(f"server-{i}" in listed_names for i in range(3))
