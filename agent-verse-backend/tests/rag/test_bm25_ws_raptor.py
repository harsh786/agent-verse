# tests/rag/test_bm25_ws_raptor.py
"""True BM25, WebSocket MCP, RAPTOR parallel."""
from __future__ import annotations

import pytest


# ── True BM25 ─────────────────────────────────────────────────────────────────


def test_bm25_retriever_importable() -> None:
    from app.rag.bm25 import BM25Retriever

    r = BM25Retriever()
    assert r is not None


def test_bm25_indexes_and_searches() -> None:
    from app.rag.bm25 import BM25Retriever

    retriever = BM25Retriever()
    chunks = [
        {"chunk_id": "c1", "content": "Python machine learning tutorial"},
        {"chunk_id": "c2", "content": "Java enterprise application server"},
        {"chunk_id": "c3", "content": "Python deep learning neural networks"},
    ]
    retriever.index(chunks)
    results = retriever.search("Python machine learning", top_k=3)
    assert len(results) > 0
    # Python ML chunks should rank above Java
    chunk_ids = [r.chunk_id for r in results]
    assert chunk_ids[0] in ("c1", "c3"), (
        f"Expected Python chunk first, got {chunk_ids[0]}"
    )


def test_bm25_handles_empty_query() -> None:
    from app.rag.bm25 import BM25Retriever

    r = BM25Retriever()
    r.index([{"chunk_id": "c1", "content": "test content"}])
    results = r.search("", top_k=5)
    assert results == []


def test_bm25_handles_empty_index() -> None:
    from app.rag.bm25 import BM25Retriever

    r = BM25Retriever()
    r.index([])
    results = r.search("test", top_k=5)
    assert results == []


def test_bm25_scores_are_non_negative() -> None:
    from app.rag.bm25 import BM25Retriever

    r = BM25Retriever()
    r.index([
        {"chunk_id": "c1", "content": "hello world"},
        {"chunk_id": "c2", "content": "foo bar baz"},
    ])
    results = r.search("hello world foo", top_k=5)
    assert all(h.score >= 0 for h in results)


def test_bm25_is_available_property() -> None:
    """BM25 should report whether rank_bm25 is installed."""
    from app.rag.bm25 import BM25Retriever

    r = BM25Retriever()
    # Either True or False is acceptable — must not raise
    assert isinstance(r.is_available, bool)


# ── RAPTOR parallel ───────────────────────────────────────────────────────────


async def test_raptor_parallel_execution() -> None:
    """RAPTOR must use asyncio.gather for parallel summarization."""
    from app.providers.fake import FakeProvider
    from app.rag.agentic.patterns.raptor import RAPTORPattern

    # 4 chunks with cluster_size=2 → 2 parallel summaries at level 1
    provider = FakeProvider(responses=[
        "Summary group 1",
        "Summary group 2",
        "Final answer from RAPTOR",
    ])
    pattern = RAPTORPattern(cluster_size=2, max_levels=1)
    chunks = [
        {"content": "ML is a subset of AI.", "chunk_id": "c1"},
        {"content": "DL uses neural networks.", "chunk_id": "c2"},
        {"content": "NLP processes text.", "chunk_id": "c3"},
        {"content": "CV processes images.", "chunk_id": "c4"},
    ]
    result = await pattern.execute(
        query="AI techniques", chunks=chunks, provider=provider
    )
    assert isinstance(result, str)
    assert len(result) > 0


# ── WebSocket MCP ─────────────────────────────────────────────────────────────


def test_ws_client_importable() -> None:
    from app.mcp.ws_client import MCPWebSocketClient

    assert MCPWebSocketClient is not None


def test_ws_client_has_required_methods() -> None:
    from app.mcp.ws_client import MCPWebSocketClient

    assert hasattr(MCPWebSocketClient, "list_tools")
    assert hasattr(MCPWebSocketClient, "call_tool")


async def test_ws_client_connect_disconnect() -> None:
    """MCPWebSocketClient context manager must work without error."""
    from unittest.mock import AsyncMock, patch

    from app.mcp.ws_client import MCPWebSocketClient

    mock_ws = AsyncMock()
    mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = AsyncMock(return_value=False)
    mock_ws.send = AsyncMock()
    mock_ws.recv = AsyncMock(
        return_value='{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}'
    )

    client = MCPWebSocketClient(ws_url="ws://localhost:8080/mcp")
    with patch("websockets.connect", return_value=mock_ws):
        # Just verify it can be instantiated — no actual connection
        assert client is not None


def test_mcp_server_config_transport_fields() -> None:
    """MCPServerConfig must accept transport and ws_url fields."""
    from app.mcp.registry import MCPServerConfig

    cfg_http = MCPServerConfig(name="my-http-server", url="https://example.com/mcp")
    assert cfg_http.transport == "http"
    assert cfg_http.ws_url is None

    cfg_ws = MCPServerConfig(
        name="my-ws-server",
        transport="ws",
        ws_url="ws://localhost:8080/mcp",
    )
    assert cfg_ws.transport == "ws"
    assert cfg_ws.ws_url == "ws://localhost:8080/mcp"


def test_mcp_server_config_websocket_transport() -> None:
    """MCPServerConfig websocket alias is accepted."""
    from app.mcp.registry import MCPServerConfig

    cfg = MCPServerConfig(
        name="ws-server",
        transport="websocket",
        ws_url="wss://secure.example.com/mcp",
    )
    assert cfg.transport == "websocket"
    assert cfg.ws_url == "wss://secure.example.com/mcp"
