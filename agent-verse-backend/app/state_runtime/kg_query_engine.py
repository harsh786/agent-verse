"""KGQueryEngine — routes KG queries based on strategy (spec §3.5 matrix)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.knowledge_graph.store import KnowledgeGraphStore

_RELATIONSHIP_RE = re.compile(
    r"\b(related to|associated with|connected to|linked to|similar to)\b", re.I
)
_DEPENDENCY_RE = re.compile(r"\b(depends on|requires|needs|uses|built on|based on)\b", re.I)
_IMPACT_RE = re.compile(
    r"\b(impact of|effect of|consequence of|caused by|leads to|affects)\b", re.I
)
_CAUSAL_RE = re.compile(r"\b(why did|what caused|root cause|triggered by|because of)\b", re.I)


@dataclass
class KGQueryResult:
    strategy_used: str
    facts: list[dict[str, Any]] = field(default_factory=list)
    entities_found: list[str] = field(default_factory=list)
    confidence: float = 0.0


class KGQueryEngine:
    def __init__(self, kg_store: KnowledgeGraphStore | None = None) -> None:
        self._kg = kg_store

    # The store is SQL-backed when a database is wired; its async ``a*`` methods
    # are the only ones that consult it. Duck-typed stores without them (test
    # doubles) fall back to the sync API.
    async def _query_nodes(self, tenant_id: str, search: str, limit: int) -> list[Any]:
        fn = getattr(self._kg, "aquery_nodes", None)
        if callable(fn):
            return list(await fn(tenant_id=tenant_id, search=search, limit=limit) or [])
        return list(self._kg.query_nodes(tenant_id=tenant_id, search=search, limit=limit) or [])

    async def _edges_for_node(self, node_id: str, tenant_id: str) -> list[Any]:
        fn = getattr(self._kg, "aget_edges_for_node", None)
        if callable(fn):
            return list(await fn(node_id, tenant_id) or [])
        return list(self._kg.get_edges_for_node(node_id=node_id, tenant_id=tenant_id) or [])

    async def _nodes_by_id(self, node_ids: list[str], tenant_id: str) -> dict[str, Any]:
        fn = getattr(self._kg, "aget_nodes", None)
        if callable(fn):
            return dict(await fn(node_ids, tenant_id))
        out: dict[str, Any] = {}
        for nid in node_ids:
            node = self._kg.get_node(nid, tenant_id)
            if node is not None:
                out[nid] = node
        return out

    def select_strategy(self, query: str) -> str:
        if _RELATIONSHIP_RE.search(query):
            return "entity"
        if _DEPENDENCY_RE.search(query):
            return "path"
        if _IMPACT_RE.search(query) or _CAUSAL_RE.search(query):
            return "impact"
        if re.match(r"^(list|get|fetch|show|count|find)\b", query.strip(), re.I):
            return "none"
        return "entity"

    async def query(self, query: str, tenant_id: str, strategy: str = "auto") -> KGQueryResult:
        if strategy == "auto":
            strategy = self.select_strategy(query)
        if strategy == "none" or self._kg is None:
            return KGQueryResult(strategy_used="none", facts=[], confidence=0.0)
        try:
            if strategy == "entity":
                return await self._entity_expansion(query, tenant_id)
            elif strategy == "path":
                return await self._path_traversal(query, tenant_id)
            elif strategy in ("community", "impact"):
                return await self._neighbourhood(query, tenant_id, strategy)
            else:
                return KGQueryResult(strategy_used=strategy, facts=[], confidence=0.0)
        except Exception:
            return KGQueryResult(strategy_used=strategy, facts=[], confidence=0.0)

    async def _entity_expansion(self, query: str, tenant_id: str) -> KGQueryResult:
        nodes = await self._query_nodes(tenant_id, query[:100], 10)
        facts = [
            {
                "entity": n.label,
                "type": str(n.node_type),
                "confidence": getattr(n, "confidence", 0.7),
            }
            for n in (nodes or [])
        ]
        return KGQueryResult(
            strategy_used="entity",
            facts=facts,
            entities_found=[n.label for n in (nodes or [])],
            confidence=0.7 if facts else 0.0,
        )

    async def _path_traversal(self, query: str, tenant_id: str) -> KGQueryResult:
        """Real edge traversal using get_edges_for_node()."""
        source_nodes = await self._query_nodes(tenant_id, query[:100], 5)

        if not source_nodes:
            return KGQueryResult(strategy_used="path", facts=[], confidence=0.0)

        # Cache all discovered nodes by ID for name lookup
        node_name_cache: dict[str, str] = {n.node_id: n.label for n in source_nodes}

        facts: list[dict[str, Any]] = []
        hops: list[tuple[Any, Any, str]] = []
        for node in source_nodes[:3]:
            edges = await self._edges_for_node(node.node_id, tenant_id)
            for edge in edges[:5]:
                neighbour_id = (
                    edge.target_node_id
                    if edge.source_node_id == node.node_id
                    else edge.source_node_id
                )
                hops.append((node, edge, neighbour_id))

        # Resolve every neighbour's name with ONE batched id lookup. This used to
        # pull the tenant's first 200 nodes (by confidence) and hope the
        # neighbour was among them — a wrong answer (raw id) for any graph
        # larger than 200 nodes, and a 200-row read per miss.
        missing = [nid for _n, _e, nid in hops if nid not in node_name_cache]
        if missing:
            node_name_cache.update(
                {nid: n.label for nid, n in (await self._nodes_by_id(missing, tenant_id)).items()}
            )
        for node, edge, neighbour_id in hops:
            neighbour_name = node_name_cache.get(neighbour_id, neighbour_id)
            edge_type_str = (
                edge.edge_type.value
                if hasattr(edge.edge_type, "value")
                else str(edge.edge_type)
            )
            facts.append(
                {
                    "from": node.label,
                    "relation": edge_type_str,
                    "to": neighbour_name,
                    "confidence": getattr(edge, "confidence", 0.65),
                }
            )

        return KGQueryResult(
            strategy_used="path",
            facts=facts,
            entities_found=[n.label for n in source_nodes],
            confidence=0.65 if facts else 0.0,
        )

    async def _neighbourhood(self, query: str, tenant_id: str, strategy: str) -> KGQueryResult:
        nodes = await self._query_nodes(tenant_id, query[:100], 8)
        facts = [{"entity": n.label, "strategy": strategy} for n in (nodes or [])]
        return KGQueryResult(strategy_used=strategy, facts=facts, confidence=0.6 if facts else 0.0)
