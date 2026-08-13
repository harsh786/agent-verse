"""Tests for KG ingestion hook and multi-hop reasoner."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from app.knowledge_graph.ingestion_hook import KGIngestionHook
from app.knowledge_graph.multi_hop import MultiHopReasoner, HopPath


# ---------------------------------------------------------------------------
# KGIngestionHook tests
# ---------------------------------------------------------------------------

class TestKGIngestionHook:
    def _make_hook(self, use_llm: bool = False) -> KGIngestionHook:
        extractor = MagicMock()
        extractor.extract_entities_deterministic.return_value = []
        kg_store = MagicMock()
        kg_store.add_node = MagicMock()
        kg_store.add_edge = MagicMock()
        return KGIngestionHook(extractor=extractor, kg_store=kg_store, use_llm=use_llm)

    @pytest.mark.asyncio
    async def test_process_returns_summary(self) -> None:
        hook = self._make_hook()
        result = await hook.process(
            chunks=["Python is a programming language."],
            document_id="doc-1",
            tenant_id="t-1",
        )
        assert "entities" in result
        assert "relations" in result
        assert isinstance(result["entities"], int)

    @pytest.mark.asyncio
    async def test_process_silent_on_store_error(self) -> None:
        extractor = MagicMock()
        extractor.extract_entities_deterministic.return_value = ["Python"]
        kg_store = MagicMock()
        kg_store.add_node.side_effect = RuntimeError("DB error")
        hook = KGIngestionHook(extractor, kg_store, use_llm=False)
        # Must not raise
        result = await hook.process(["text"], "doc-1", "t-1")
        assert result["entities"] == 0  # all failed silently

    @pytest.mark.asyncio
    async def test_process_empty_chunks(self) -> None:
        hook = self._make_hook()
        result = await hook.process([], "doc-1", "t-1")
        assert result["entities"] == 0


# ---------------------------------------------------------------------------
# MultiHopReasoner tests
# ---------------------------------------------------------------------------

class FakeKGStore:
    """In-memory KG store for tests."""

    def __init__(self, edges: dict[str, list[dict]]) -> None:
        self._edges = edges  # {node: [{target, relation}]}

    def get_neighbors(self, node_id: str) -> list[dict]:
        return self._edges.get(node_id, [])

    def get_node(self, node_id: str) -> dict:
        return {"id": node_id, "label": node_id}


class TestMultiHopReasoner:
    def _make_store(self) -> FakeKGStore:
        return FakeKGStore({
            "Alice": [{"target": "Bob", "relation": "KNOWS"}],
            "Bob": [{"target": "Charlie", "relation": "WORKS_WITH"}],
            "Charlie": [{"target": "Alice", "relation": "FRIEND_OF"}],
        })

    def test_find_direct_path(self) -> None:
        store = self._make_store()
        reasoner = MultiHopReasoner(store)
        paths = reasoner.find_paths("Alice", "Bob", max_hops=1)
        assert len(paths) >= 1
        assert paths[0].nodes[0] == "Alice"
        assert paths[0].nodes[-1] == "Bob"

    def test_find_two_hop_path(self) -> None:
        store = self._make_store()
        reasoner = MultiHopReasoner(store)
        paths = reasoner.find_paths("Alice", "Charlie", max_hops=2)
        assert any(p.length == 2 for p in paths)

    def test_no_path_beyond_max_hops(self) -> None:
        store = self._make_store()
        reasoner = MultiHopReasoner(store)
        paths = reasoner.find_paths("Alice", "Charlie", max_hops=1)
        # 2-hop path should not appear when max_hops=1
        assert all(p.length <= 1 for p in paths)

    def test_retrieve_subgraph(self) -> None:
        store = self._make_store()
        reasoner = MultiHopReasoner(store)
        sg = reasoner.retrieve_subgraph("Alice", depth=1)
        assert sg.center == "Alice"
        assert len(sg.nodes) >= 2  # Alice + Bob
        assert len(sg.edges) >= 1

    def test_paths_to_context_text(self) -> None:
        paths = [HopPath(nodes=["A", "B", "C"], edges=["KNOWS", "WORKS_WITH"])]
        reasoner = MultiHopReasoner(FakeKGStore({}))
        text = reasoner.paths_to_context(paths)
        assert "A" in text
        assert "KNOWS" in text
