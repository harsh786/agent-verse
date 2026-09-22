"""Tests for CommunityDetector — BFS/Union-Find connected-components community
detection. Focuses on the shallow-coverage edges: disconnected components,
single-node and empty graphs, unknown-node edges, self-loops, and a larger
graph to sanity-check the O(n*a(n)) union-find still produces correct
components (not a perf benchmark, just correctness at size)."""
from __future__ import annotations

from types import SimpleNamespace

from app.knowledge_graph.community_detection import CommunityDetector
from app.knowledge_graph.models import EdgeType, GraphEdge, GraphNode, NodeType


def _edge(src: str, tgt: str) -> SimpleNamespace:
    """Minimal edge-like object exposing the two attributes the detector reads."""
    return SimpleNamespace(source_node_id=src, target_node_id=tgt)


def _graph_node(node_id: str) -> GraphNode:
    return GraphNode(
        node_id=node_id,
        tenant_id="t1",
        node_type=NodeType.ENTITY,
        label=node_id,
    )


def _graph_edge(edge_id: str, src: str, tgt: str) -> GraphEdge:
    return GraphEdge(
        edge_id=edge_id,
        tenant_id="t1",
        source_node_id=src,
        target_node_id=tgt,
        edge_type=EdgeType.MENTIONS,
    )


class TestEmptyAndSingleNodeGraphs:
    def test_empty_graph_returns_no_communities(self) -> None:
        assert CommunityDetector().detect_communities([], []) == []

    def test_empty_nodes_with_edges_returns_no_communities(self) -> None:
        # No nodes at all — even with (bogus) edges, nothing to group.
        result = CommunityDetector().detect_communities([], [_edge("a", "b")])
        assert result == []

    def test_single_node_no_edges_is_not_a_community(self) -> None:
        # Isolated singletons are excluded (size < 2 skipped).
        result = CommunityDetector().detect_communities(["solo"], [])
        assert result == []

    def test_single_node_with_self_loop_still_not_a_community(self) -> None:
        result = CommunityDetector().detect_communities(["solo"], [_edge("solo", "solo")])
        assert result == []


class TestDisconnectedComponents:
    def test_two_disjoint_pairs_form_two_communities(self) -> None:
        nodes = ["a", "b", "c", "d"]
        edges = [_edge("a", "b"), _edge("c", "d")]
        communities = CommunityDetector().detect_communities(nodes, edges)

        assert len(communities) == 2
        node_id_sets = [set(c["node_ids"]) for c in communities]
        assert {"a", "b"} in node_id_sets
        assert {"c", "d"} in node_id_sets

    def test_isolated_node_excluded_from_connected_components(self) -> None:
        nodes = ["a", "b", "isolated"]
        edges = [_edge("a", "b")]
        communities = CommunityDetector().detect_communities(nodes, edges)

        assert len(communities) == 1
        assert communities[0]["node_ids"] == ["a", "b"] or set(
            communities[0]["node_ids"]
        ) == {"a", "b"}
        all_members = {nid for c in communities for nid in c["node_ids"]}
        assert "isolated" not in all_members

    def test_three_separate_clusters(self) -> None:
        nodes = ["a1", "a2", "b1", "b2", "b3", "c1", "c2"]
        edges = [
            _edge("a1", "a2"),
            _edge("b1", "b2"),
            _edge("b2", "b3"),
            _edge("c1", "c2"),
        ]
        communities = CommunityDetector().detect_communities(nodes, edges)
        sizes = sorted(c["size"] for c in communities)
        assert sizes == [2, 2, 3]


class TestEdgeValidation:
    def test_edge_referencing_unknown_node_is_ignored(self) -> None:
        nodes = ["a", "b"]
        edges = [_edge("a", "b"), _edge("a", "ghost"), _edge("ghost2", "b")]
        communities = CommunityDetector().detect_communities(nodes, edges)

        assert len(communities) == 1
        assert set(communities[0]["node_ids"]) == {"a", "b"}
        # Unknown nodes never appear anywhere in the output.
        all_members = {nid for c in communities for nid in c["node_ids"]}
        assert "ghost" not in all_members
        assert "ghost2" not in all_members

    def test_edge_missing_source_or_target_attribute_is_ignored(self) -> None:
        nodes = ["a", "b"]
        bad_edge = SimpleNamespace(source_node_id="a")  # no target_node_id at all
        communities = CommunityDetector().detect_communities(nodes, [bad_edge])
        assert communities == []

    def test_plain_string_edge_is_ignored_not_crashed(self) -> None:
        # A bare string edge has no source/target attrs to read — the detector
        # must skip it gracefully rather than raising.
        nodes = ["a", "b"]
        communities = CommunityDetector().detect_communities(nodes, ["not-an-edge"])
        assert communities == []


class TestCentralNodeAndDensity:
    def test_central_node_is_highest_degree_hub(self) -> None:
        # Star graph: hub connected to 4 leaves.
        nodes = ["hub", "l1", "l2", "l3", "l4"]
        edges = [_edge("hub", "l1"), _edge("hub", "l2"), _edge("hub", "l3"), _edge("hub", "l4")]
        communities = CommunityDetector().detect_communities(nodes, edges)

        assert len(communities) == 1
        assert communities[0]["central_node"] == "hub"
        assert communities[0]["size"] == 5

    def test_density_for_fully_connected_triangle_is_one(self) -> None:
        nodes = ["a", "b", "c"]
        edges = [_edge("a", "b"), _edge("b", "c"), _edge("a", "c")]
        communities = CommunityDetector().detect_communities(nodes, edges)

        assert len(communities) == 1
        assert communities[0]["density"] == 1.0

    def test_density_for_path_graph_is_fractional(self) -> None:
        # Path a-b-c: 2 edges out of 3 possible pairs => density 2/3.
        nodes = ["a", "b", "c"]
        edges = [_edge("a", "b"), _edge("b", "c")]
        communities = CommunityDetector().detect_communities(nodes, edges)

        assert len(communities) == 1
        assert communities[0]["density"] == round(2 / 3, 4)

    def test_duplicate_edges_between_same_pair_inflate_density(self) -> None:
        # Documents current behaviour: the detector counts each edge record
        # toward density, so repeated edges between the same pair are not
        # de-duplicated before the density ratio is computed.
        nodes = ["a", "b"]
        edges = [_edge("a", "b"), _edge("a", "b"), _edge("a", "b")]
        communities = CommunityDetector().detect_communities(nodes, edges)

        assert len(communities) == 1
        assert communities[0]["density"] == 3.0  # 3 edges / 1 possible pair


class TestLargerGraphCorrectness:
    def test_ring_of_many_nodes_forms_single_community(self) -> None:
        # Correctness at size, not a perf benchmark: a 300-node ring should
        # collapse to exactly one community containing every node.
        n = 300
        nodes = [f"n{i}" for i in range(n)]
        edges = [_edge(f"n{i}", f"n{(i + 1) % n}") for i in range(n)]

        communities = CommunityDetector().detect_communities(nodes, edges)

        assert len(communities) == 1
        assert communities[0]["size"] == n
        assert set(communities[0]["node_ids"]) == set(nodes)

    def test_many_small_disjoint_pairs_stay_separate(self) -> None:
        n_pairs = 150
        nodes = [f"p{i}a" for i in range(n_pairs)] + [f"p{i}b" for i in range(n_pairs)]
        edges = [_edge(f"p{i}a", f"p{i}b") for i in range(n_pairs)]

        communities = CommunityDetector().detect_communities(nodes, edges)

        assert len(communities) == n_pairs
        assert all(c["size"] == 2 for c in communities)


class TestRealGraphNodeAndEdgeObjects:
    def test_accepts_graphnode_and_graphedge_dataclasses(self) -> None:
        nodes = [_graph_node("a"), _graph_node("b"), _graph_node("c")]
        edges = [_graph_edge("e1", "a", "b"), _graph_edge("e2", "b", "c")]

        communities = CommunityDetector().detect_communities(nodes, edges)

        assert len(communities) == 1
        assert set(communities[0]["node_ids"]) == {"a", "b", "c"}


class TestGetNodeCommunity:
    def test_returns_community_id_for_member_node(self) -> None:
        nodes = ["a", "b"]
        edges = [_edge("a", "b")]
        detector = CommunityDetector()
        communities = detector.detect_communities(nodes, edges)

        found = detector.get_node_community("a", communities)
        assert found == communities[0]["community_id"]

    def test_returns_none_for_node_not_in_any_community(self) -> None:
        nodes = ["a", "b", "solo"]
        edges = [_edge("a", "b")]
        detector = CommunityDetector()
        communities = detector.detect_communities(nodes, edges)

        assert detector.get_node_community("solo", communities) is None
        assert detector.get_node_community("nonexistent", communities) is None

    def test_returns_none_for_empty_community_list(self) -> None:
        assert CommunityDetector().get_node_community("a", []) is None


class TestRankCommunities:
    def test_sorts_by_size_descending(self) -> None:
        nodes = ["a", "b", "c", "d", "e", "f"]
        edges = [_edge("a", "b"), _edge("c", "d"), _edge("d", "e"), _edge("e", "f")]
        detector = CommunityDetector()
        communities = detector.detect_communities(nodes, edges)

        ranked = detector.rank_communities(communities)
        sizes = [c["size"] for c in ranked]
        assert sizes == sorted(sizes, reverse=True)

    def test_empty_list_returns_empty(self) -> None:
        assert CommunityDetector().rank_communities([]) == []
