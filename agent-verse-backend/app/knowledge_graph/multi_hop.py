"""Multi-hop graph reasoning over the AgentVerse knowledge graph.

Provides BFS-based path finding between entities and ego-network subgraph
extraction for use in graph-augmented RAG.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HopPath:
    nodes: list[str]
    edges: list[str]  # relation labels along the path

    @property
    def length(self) -> int:
        return len(self.nodes) - 1

    def as_text(self) -> str:
        """Render path as a readable fact chain."""
        if len(self.nodes) < 2:
            return self.nodes[0] if self.nodes else ""
        parts: list[str] = []
        for i, node in enumerate(self.nodes[:-1]):
            parts.append(f"{node} --[{self.edges[i]}]--> {self.nodes[i + 1]}")
        return " | ".join(parts)


@dataclass
class Subgraph:
    center: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)

    def as_text(self) -> str:
        lines = [f"Entity: {self.center}"]
        for edge in self.edges:
            lines.append(
                f"  {edge.get('source', '')} --[{edge.get('relation', '')}]--> {edge.get('target', '')}"
            )
        return "\n".join(lines)


class MultiHopReasoner:
    """BFS multi-hop path finding and ego-network extraction over a KG store.

    The *store* must expose:
    - `get_neighbors(node_id: str) -> list[dict]` — each dict has keys
      `target`, `relation`, and optionally `weight`.
    - `get_node(node_id: str) -> dict | None` — node metadata.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_paths(
        self,
        start: str,
        end: str,
        max_hops: int = 3,
    ) -> list[HopPath]:
        """BFS from *start* to *end*, returning all paths up to *max_hops*.

        Parameters
        ----------
        start : str
            Starting entity ID / label.
        end : str
            Target entity ID / label.
        max_hops : int
            Maximum path length (number of edges).

        Returns
        -------
        list[HopPath]
            All discovered paths, shortest first.
        """
        found: list[HopPath] = []
        # Queue items: (current_node, path_nodes, path_edges)
        queue: deque[tuple[str, list[str], list[str]]] = deque()
        queue.append((start, [start], []))
        visited_per_path: set[tuple[str, ...]] = set()

        while queue:
            current, nodes, edges = queue.popleft()
            if len(edges) > max_hops:
                continue
            state = tuple(nodes)
            if state in visited_per_path:
                continue
            visited_per_path.add(state)

            if current == end and len(edges) > 0:
                found.append(HopPath(nodes=list(nodes), edges=list(edges)))
                continue  # don't explore beyond the target

            if len(edges) >= max_hops:
                continue

            neighbours = self._neighbours(current)
            for nbr in neighbours:
                target = nbr.get("target", "")
                relation = nbr.get("relation", "RELATED_TO")
                if target not in nodes:  # avoid cycles
                    queue.append(
                        (
                            target,
                            [*nodes, target],
                            [*edges, relation],
                        )
                    )

        found.sort(key=lambda p: p.length)
        return found

    def retrieve_subgraph(
        self,
        entity: str,
        depth: int = 2,
    ) -> Subgraph:
        """Extract the ego-network around *entity* up to *depth* hops.

        Parameters
        ----------
        entity : str
            Center entity label or ID.
        depth : int
            Number of hops to expand from the center.
        """
        visited_nodes: set[str] = {entity}
        all_edges: list[dict[str, Any]] = []
        frontier = {entity}

        for _ in range(depth):
            next_frontier: set[str] = set()
            for node in frontier:
                for nbr in self._neighbours(node):
                    target = nbr.get("target", "")
                    all_edges.append(
                        {
                            "source": node,
                            "target": target,
                            "relation": nbr.get("relation", "RELATED_TO"),
                        }
                    )
                    if target not in visited_nodes:
                        visited_nodes.add(target)
                        next_frontier.add(target)
            frontier = next_frontier
            if not frontier:
                break

        nodes = []
        for n in visited_nodes:
            meta = self._get_node(n)
            nodes.append(meta if meta else {"id": n, "label": n})

        return Subgraph(center=entity, nodes=nodes, edges=all_edges)

    def paths_to_context(self, paths: list[HopPath]) -> str:
        """Convert paths to a text block suitable for RAG context injection."""
        if not paths:
            return ""
        lines = ["Knowledge graph reasoning paths:"]
        for path in paths[:5]:  # cap at 5 paths
            lines.append(f"  {path.as_text()}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _neighbours(self, node_id: str) -> list[dict[str, Any]]:
        try:
            result = self._store.get_neighbors(node_id)
            return result if isinstance(result, list) else []
        except Exception:
            return []

    def _get_node(self, node_id: str) -> dict[str, Any] | None:
        try:
            return self._store.get_node(node_id)
        except Exception:
            return None
