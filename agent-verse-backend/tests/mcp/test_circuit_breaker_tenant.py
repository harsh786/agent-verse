"""Tests for per-tenant circuit breaker isolation."""
import pytest
from unittest.mock import AsyncMock, MagicMock


def test_circuit_breaker_key_is_tenant_scoped():
    """Two different tenants must get different circuit breaker instances."""
    from app.mcp.client import MCPClient
    client = MCPClient(registry=MagicMock(), redis=None)

    cb1 = client._get_circuit_breaker("github-mcp", tenant_id="tenant-a")
    cb2 = client._get_circuit_breaker("github-mcp", tenant_id="tenant-b")
    cb3 = client._get_circuit_breaker("github-mcp", tenant_id="tenant-a")  # same as cb1

    assert cb1 is not cb2, "Different tenants must have different circuit breakers"
    assert cb1 is cb3, "Same tenant must get same circuit breaker instance (cached)"


def test_empty_tenant_id_creates_separate_key():
    """Empty tenant_id creates a distinct key from real tenant IDs."""
    from app.mcp.client import MCPClient
    client = MCPClient(registry=MagicMock(), redis=None)

    cb_empty = client._get_circuit_breaker("srv1", tenant_id="")
    cb_tenant = client._get_circuit_breaker("srv1", tenant_id="tenant-1")

    assert cb_empty is not cb_tenant
