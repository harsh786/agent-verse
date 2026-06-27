"""Tests for P2.3 tool reliability memory."""
import pytest
from unittest.mock import AsyncMock


@pytest.mark.asyncio
async def test_tool_reliability_record_success():
    from app.memory.tool_reliability import ToolReliabilityStore

    store = ToolReliabilityStore(db_session_factory=None)
    await store.record(
        tenant_id="t1", tool_name="jira.search", success=True, latency_ms=150
    )
    stats = await store.get_reliability(tenant_id="t1", tool_name="jira.search")
    assert stats["success_count"] == 1
    assert stats["failure_count"] == 0
    assert stats["success_rate"] == 1.0


@pytest.mark.asyncio
async def test_tool_reliability_record_failure():
    from app.memory.tool_reliability import ToolReliabilityStore

    store = ToolReliabilityStore(db_session_factory=None)
    await store.record(
        tenant_id="t1", tool_name="jira.delete", success=False, latency_ms=50
    )
    await store.record(
        tenant_id="t1", tool_name="jira.delete", success=True, latency_ms=100
    )
    stats = await store.get_reliability(tenant_id="t1", tool_name="jira.delete")
    assert stats["success_count"] == 1
    assert stats["failure_count"] == 1
    assert stats["success_rate"] == 0.5


@pytest.mark.asyncio
async def test_tool_reliability_no_db_get_unreliable_empty():
    from app.memory.tool_reliability import ToolReliabilityStore

    store = ToolReliabilityStore(db_session_factory=None)
    result = await store.get_unreliable_tools(tenant_id="t1")
    assert result == []


def test_tool_reliability_api_endpoint_exists():
    from app.main import create_app

    app = create_app()
    # Use OpenAPI spec for reliable route enumeration
    paths = list(app.openapi().get("paths", {}).keys())
    assert any(
        "tool-reliability" in p for p in paths
    ), f"/memory/tool-reliability endpoint must exist. Found: {paths[:10]}"


def test_migration_0037_exists():
    import os

    files = os.listdir(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/"
        "agent-verse-backend/app/db/migrations/versions"
    )
    assert any("0037" in f for f in files)
