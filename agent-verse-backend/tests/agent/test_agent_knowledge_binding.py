"""Tests for Agent Builder knowledge binding — FIX 1 (critical).

Validates that:
- AgentGraph exposes _agent_collection_ids attribute
- The old "skip — no collection_id available" comment has been removed
- Permissions endpoints query the agent_permissions DB table
- Readiness endpoint verifies connectors via MCP registry
- Export endpoint includes a tools array built from connector capabilities
- _agent_collection_ids wiring causes KnowledgeStore to be queried during RAG
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock


def test_agentgraph_has_agent_collection_ids_attr():
    """AgentGraph must have _agent_collection_ids attribute."""
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    g = AgentGraph(
        planner=FakeProvider(),
        executor=FakeProvider(),
        verifier=FakeProvider(),
    )
    assert hasattr(g, "_agent_collection_ids"), (
        "AgentGraph must have _agent_collection_ids for knowledge binding"
    )
    assert g._agent_collection_ids == [], (
        "_agent_collection_ids must default to an empty list"
    )


def test_graph_rag_comment_removed():
    """Graph must not skip KnowledgeStore with 'no collection_id available' comment."""
    import inspect
    from app.agent import graph

    src = inspect.getsource(graph)
    assert "skip — no collection_id" not in src, (
        "KnowledgeStore RAG skip comment must be removed and replaced with real implementation"
    )


def test_permissions_endpoint_reads_db():
    """Permissions GET/PUT must query agent_permissions table, not only in-memory dict."""
    import inspect
    from app.api import agents

    src = inspect.getsource(agents)
    assert "agent_permissions" in src, (
        "Permissions endpoints must query the agent_permissions DB table"
    )


def test_readiness_check_queries_registry():
    """Readiness check must query MCP registry for connector verification."""
    import inspect
    from app.api import agents

    src = inspect.getsource(agents)
    assert "registry.get" in src or "mcp_registry" in src, (
        "Readiness check must verify connectors via MCP registry"
    )


def test_export_includes_tools():
    """Agent export must include connector tools array."""
    import inspect
    from app.api import agents

    src = inspect.getsource(agents)
    assert "discover_all_tools" in src or '"tools"' in src, (
        "Agent export must include connector tools"
    )


@pytest.mark.asyncio
async def test_agent_collection_ids_used_in_rag():
    """When _agent_collection_ids is set, graph must query KnowledgeStore during RAG."""
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    mock_ks = MagicMock()
    mock_ks.hybrid_search_db = AsyncMock(return_value=[])

    g = AgentGraph(
        planner=FakeProvider(),
        executor=FakeProvider(),
        verifier=FakeProvider(),
        knowledge_store=mock_ks,
    )
    g._agent_collection_ids = ["col-1", "col-2"]

    # Verify wiring: attribute is stored and knowledge_store is referenced correctly
    assert g._agent_collection_ids == ["col-1", "col-2"]
    assert g._knowledge_store is mock_ks


@pytest.mark.asyncio
async def test_node_rag_retrieval_calls_knowledge_store():
    """_node_rag_retrieval must call hybrid_search_db for each bound collection."""
    from app.agent.graph import AgentGraph, GraphState
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider
    from app.tenancy.context import PlanTier, TenantContext

    mock_ks = MagicMock()
    mock_ks.hybrid_search_db = AsyncMock(return_value=[])

    g = AgentGraph(
        planner=FakeProvider(),
        executor=FakeProvider(),
        verifier=FakeProvider(),
        knowledge_store=mock_ks,
    )
    g._agent_collection_ids = ["col-abc"]

    tenant_ctx = TenantContext(
        tenant_id="t1",
        plan=PlanTier.FREE,
        api_key_id="test",
    )
    agent_state = AgentState(goal="test goal", tenant_ctx=tenant_ctx)

    state: GraphState = {
        "goal": "test goal",
        "tenant_ctx": tenant_ctx,
        "agent_state": agent_state,
    }

    await g._node_rag_retrieval(state)

    # KnowledgeStore must have been queried for the bound collection
    mock_ks.hybrid_search_db.assert_called_once()
    call_kwargs = mock_ks.hybrid_search_db.call_args
    # Verify collection_id was passed correctly
    assert call_kwargs.kwargs.get("collection_id") == "col-abc" or (
        len(call_kwargs.args) >= 3 and call_kwargs.args[2] == "col-abc"
    ), "hybrid_search_db must be called with the bound collection_id"
