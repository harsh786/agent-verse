"""Tests for the multi-hop reasoner against a lightweight fake store.

The ingestion-hook tests moved to ``test_ingestion_hook.py`` (the old
``KGIngestionHook`` class was replaced by ``extract_and_store_graph``).
Multi-hop traversal against the *real* ``KnowledgeGraphStore.get_neighbors`` is
covered in ``test_multi_hop.py``.
"""

from __future__ import annotations

from app.knowledge_graph.multi_hop import HopPath, MultiHopReasoner


class FakeKGStore:
    """In-memory KG store for tests (tenant-less ``get_neighbors``)."""

    def __init__(self, edges: dict[str, list[dict[str, str]]]) -> None:
        self._edges = edges  # {node: [{target, relation}]}

    def get_neighbors(self, node_id: str) -> list[dict[str, str]]:
        return self._edges.get(node_id, [])

    def get_node(self, node_id: str) -> dict[str, str]:
        return {"id": node_id, "label": node_id}


class TestMultiHopReasoner:
    def _make_store(self) -> FakeKGStore:
        return FakeKGStore(
            {
                "Alice": [{"target": "Bob", "relation": "KNOWS"}],
                "Bob": [{"target": "Charlie", "relation": "WORKS_WITH"}],
                "Charlie": [{"target": "Alice", "relation": "FRIEND_OF"}],
            }
        )

    def test_find_direct_path(self) -> None:
        reasoner = MultiHopReasoner(self._make_store())
        paths = reasoner.find_paths("Alice", "Bob", max_hops=1)
        assert len(paths) >= 1
        assert paths[0].nodes[0] == "Alice"
        assert paths[0].nodes[-1] == "Bob"

    def test_find_two_hop_path(self) -> None:
        reasoner = MultiHopReasoner(self._make_store())
        paths = reasoner.find_paths("Alice", "Charlie", max_hops=2)
        assert any(p.length == 2 for p in paths)

    def test_no_path_beyond_max_hops(self) -> None:
        reasoner = MultiHopReasoner(self._make_store())
        paths = reasoner.find_paths("Alice", "Charlie", max_hops=1)
        assert all(p.length <= 1 for p in paths)

    def test_retrieve_subgraph(self) -> None:
        reasoner = MultiHopReasoner(self._make_store())
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
