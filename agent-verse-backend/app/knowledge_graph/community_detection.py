"""BFS/Union-Find community detection for the Knowledge Graph."""
from __future__ import annotations

import uuid
from typing import Any


class CommunityDetector:
    """BFS-based connected-components community detection for the KG.

    Uses Union-Find (disjoint sets) for O(n*a(n)) time complexity.
    """

    def detect_communities(self, nodes: list[Any], edges: list[Any]) -> list[dict[str, Any]]:
        """Return list of community dicts.

        Each dict has:
          community_id  - UUID string
          node_ids      - list of node id strings in this community
          size          - number of nodes
          central_node  - node_id with the highest degree
          density       - edges within community / possible undirected edges
        """
        if not nodes:
            return []

        # Normalise: accept GraphNode objects or plain strings
        def _node_id(n: Any) -> str:
            return n if isinstance(n, str) else n.node_id

        node_id_list = [_node_id(n) for n in nodes]
        node_id_set = set(node_id_list)

        # ── Union-Find ────────────────────────────────────────────────────────
        parent: dict[str, str] = {nid: nid for nid in node_id_list}
        rank: dict[str, int] = dict.fromkeys(node_id_list, 0)

        def _find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]  # path halving
                x = parent[x]
            return x

        def _union(a: str, b: str) -> None:
            ra, rb = _find(a), _find(b)
            if ra == rb:
                return
            if rank[ra] < rank[rb]:
                ra, rb = rb, ra
            parent[rb] = ra
            if rank[ra] == rank[rb]:
                rank[ra] += 1

        # ── Process edges ─────────────────────────────────────────────────────
        degree: dict[str, int] = dict.fromkeys(node_id_list, 0)
        valid_pairs: list[tuple[str, str]] = []

        for e in edges:
            src = e if isinstance(e, str) else getattr(e, "source_node_id", None)
            tgt = None if isinstance(e, str) else getattr(e, "target_node_id", None)
            if src is None or tgt is None:
                continue
            if src not in node_id_set or tgt not in node_id_set:
                continue
            valid_pairs.append((src, tgt))
            _union(src, tgt)
            degree[src] = degree.get(src, 0) + 1
            degree[tgt] = degree.get(tgt, 0) + 1

        # ── Group nodes by component root ─────────────────────────────────────
        components: dict[str, list[str]] = {}
        for nid in node_id_list:
            root = _find(nid)
            components.setdefault(root, []).append(nid)

        # Count intra-community edges per component
        intra_edge_count: dict[str, int] = {}
        for src, _ in valid_pairs:
            root = _find(src)
            intra_edge_count[root] = intra_edge_count.get(root, 0) + 1

        # ── Build community dicts ─────────────────────────────────────────────
        communities: list[dict[str, Any]] = []
        for root, members in components.items():
            if len(members) < 2:
                continue  # skip isolated singletons

            central_node = max(members, key=lambda nid: degree.get(nid, 0))
            n = len(members)
            possible_edges = n * (n - 1) / 2.0  # undirected
            edges_in = intra_edge_count.get(root, 0)
            density = edges_in / possible_edges if possible_edges > 0 else 0.0

            communities.append({
                "community_id": str(uuid.uuid4()),
                "node_ids": members,
                "size": n,
                "central_node": central_node,
                "density": round(density, 4),
            })

        return communities

    def get_node_community(
        self, node_id: str, communities: list[dict[str, Any]]
    ) -> str | None:
        """Return the community_id for a given node_id, or None if not found."""
        for community in communities:
            if node_id in community.get("node_ids", []):
                return community["community_id"]
        return None

    def rank_communities(
        self, communities: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Sort communities by size descending, return ranked list."""
        return sorted(communities, key=lambda c: c.get("size", 0), reverse=True)
