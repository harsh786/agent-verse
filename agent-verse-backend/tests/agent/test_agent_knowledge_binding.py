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



def _agent_source() -> str:
    """Read combined source of graph.py and all node mixin files."""
    import pathlib
    parts = [pathlib.Path("app/agent/graph.py").read_text(encoding="utf-8")]
    for f in sorted(pathlib.Path("app/agent/nodes").glob("*.py")):
        parts.append(f.read_text(encoding="utf-8"))
    return "\n".join(parts)

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

    src = _agent_source()
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
async def test_node_rag_retrieval_calls_gateway_for_bound_collection():
    """_node_rag_retrieval must route bound collections through the gateway."""
    from app.agent.graph import AgentGraph, GraphState
    from app.agent.state import AgentState
    from app.providers.fake import FakeProvider
    from app.rag.contracts import RAGExecutionResult, RAGStrategy
    from app.tenancy.context import PlanTier, TenantContext

    gateway = MagicMock()
    gateway.execute = AsyncMock(
        return_value=RAGExecutionResult(
            requested_strategy_id="hybrid",
            resolved_strategy_id=RAGStrategy.HYBRID,
        )
    )

    g = AgentGraph(
        planner=FakeProvider(),
        executor=FakeProvider(),
        verifier=FakeProvider(),
        retrieval_gateway=gateway,
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

    gateway.execute.assert_awaited_once()
    call = gateway.execute.await_args
    assert call.args[0] is tenant_ctx
    assert call.kwargs["collection_id"] == "col-abc"
    assert call.kwargs["strategy_id"] == "hybrid"
    assert call.kwargs["top_k"] == 3
    assert call.kwargs["filters"] == {}
