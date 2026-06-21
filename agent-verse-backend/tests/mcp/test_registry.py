"""Tests for Phase 4 — MCP connector registry (Redis-backed, per-tenant).

Tests cover:
- MCPServerConfig model (9 auth types, validation)
- MCPRegistry: register/get/list/unregister, tenant isolation
- Connector catalog lookup
- A2A AgentCard model
"""

from __future__ import annotations

import pytest

from app.mcp.registry import MCPRegistry, MCPServerConfig, ServerStatus
from app.mcp.catalog import CONNECTOR_CATALOG, ConnectorSpec
from app.mcp.a2a import AgentCard
from app.tenancy.context import PlanTier, TenantContext

_CTX_A = TenantContext(tenant_id="tid-a", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
_CTX_B = TenantContext(tenant_id="tid-b", plan=PlanTier.STARTER, api_key_id="k2")


# ── MCPServerConfig model ──────────────────────────────────────────────────────

def test_server_config_bearer_auth() -> None:
    cfg = MCPServerConfig(
        name="github",
        url="http://localhost:8001",
        auth_type="bearer",
        auth_config={"token": "ghp_xxx"},
    )
    assert cfg.auth_type == "bearer"


def test_server_config_all_9_auth_types() -> None:
    auth_types = [
        "bearer", "api_key", "oauth_ac", "oauth_cc", "pkce",
        "basic", "custom_header", "mtls", "hmac",
    ]
    for auth in auth_types:
        cfg = MCPServerConfig(
            name="test",
            url="http://localhost",
            auth_type=auth,
            auth_config={},
        )
        assert cfg.auth_type == auth


def test_server_config_rejects_unknown_auth_type() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MCPServerConfig(
            name="test",
            url="http://localhost",
            auth_type="unknown_type",
            auth_config={},
        )


def test_server_config_status_default_is_active() -> None:
    cfg = MCPServerConfig(
        name="jira",
        url="http://localhost:8002",
        auth_type="api_key",
        auth_config={"key": "xxx"},
    )
    assert cfg.status == ServerStatus.ACTIVE


# ── MCPRegistry (in-memory FakeRedis) ─────────────────────────────────────────

class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}

    async def set(self, key: str, value: str) -> None:
        self._store[key] = value

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def delete(self, *keys: str) -> int:
        count = sum(1 for k in keys if k in self._store)
        for k in keys:
            self._store.pop(k, None)
        return count

    async def sadd(self, key: str, *members: str) -> int:
        self._sets.setdefault(key, set()).update(members)
        return len(members)

    async def smembers(self, key: str) -> set[str]:
        return self._sets.get(key, set())

    async def srem(self, key: str, *members: str) -> int:
        s = self._sets.get(key, set())
        removed = sum(1 for m in members if m in s)
        s.difference_update(members)
        return removed


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


def _cfg(name: str, url: str = "http://localhost:9000") -> MCPServerConfig:
    return MCPServerConfig(name=name, url=url, auth_type="bearer", auth_config={"token": "t"})


async def test_registry_register_and_get(fake_redis: FakeRedis) -> None:
    reg = MCPRegistry(redis=fake_redis)
    cfg = _cfg("github")
    server_id = await reg.register(cfg, tenant_ctx=_CTX_A)
    assert server_id
    fetched = await reg.get(server_id, tenant_ctx=_CTX_A)
    assert fetched is not None
    assert fetched.name == "github"


async def test_registry_tenant_isolation(fake_redis: FakeRedis) -> None:
    reg = MCPRegistry(redis=fake_redis)
    cfg = _cfg("slack")
    server_id = await reg.register(cfg, tenant_ctx=_CTX_A)
    # Tenant B should NOT see tenant A's server
    fetched = await reg.get(server_id, tenant_ctx=_CTX_B)
    assert fetched is None


async def test_registry_list_returns_only_own_servers(fake_redis: FakeRedis) -> None:
    reg = MCPRegistry(redis=fake_redis)
    await reg.register(_cfg("github"), tenant_ctx=_CTX_A)
    await reg.register(_cfg("jira"), tenant_ctx=_CTX_A)
    await reg.register(_cfg("slack"), tenant_ctx=_CTX_B)

    servers_a = await reg.list_servers(tenant_ctx=_CTX_A)
    servers_b = await reg.list_servers(tenant_ctx=_CTX_B)
    assert len(servers_a) == 2
    assert len(servers_b) == 1


async def test_registry_unregister(fake_redis: FakeRedis) -> None:
    reg = MCPRegistry(redis=fake_redis)
    server_id = await reg.register(_cfg("linear"), tenant_ctx=_CTX_A)
    removed = await reg.unregister(server_id, tenant_ctx=_CTX_A)
    assert removed is True
    assert await reg.get(server_id, tenant_ctx=_CTX_A) is None


async def test_registry_unregister_wrong_tenant(fake_redis: FakeRedis) -> None:
    reg = MCPRegistry(redis=fake_redis)
    server_id = await reg.register(_cfg("notion"), tenant_ctx=_CTX_A)
    removed = await reg.unregister(server_id, tenant_ctx=_CTX_B)
    assert removed is False


# ── Connector catalog ─────────────────────────────────────────────────────────

def test_catalog_contains_expected_connectors() -> None:
    names = {spec.name for spec in CONNECTOR_CATALOG}
    expected = {"github", "jira", "slack", "salesforce", "linear", "notion", "sentry", "datadog", "stripe"}
    assert expected.issubset(names)


def test_catalog_each_spec_has_required_fields() -> None:
    for spec in CONNECTOR_CATALOG:
        assert spec.name
        assert spec.description
        assert spec.auth_type
        assert spec.default_url


# ── A2A AgentCard ─────────────────────────────────────────────────────────────

def test_agent_card_serializes() -> None:
    card = AgentCard(
        name="my-agent",
        version="1.0.0",
        capabilities=["text", "tools"],
        endpoint="https://example.com/a2a",
    )
    d = card.model_dump()
    assert d["name"] == "my-agent"
    assert "capabilities" in d


def test_agent_card_default_capabilities_empty() -> None:
    card = AgentCard(name="x", version="0.1", endpoint="http://localhost")
    assert card.capabilities == []
