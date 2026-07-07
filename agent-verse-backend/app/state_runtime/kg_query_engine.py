"""KGQueryEngine — routes KG queries based on strategy (spec §3.5 matrix)."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.knowledge_graph.store import KnowledgeGraphStore

_RELATIONSHIP_RE = re.compile(r"\b(related to|associated with|connected to|linked to|similar to)\b", re.I)
_DEPENDENCY_RE = re.compile(r"\b(depends on|requires|needs|uses|built on|based on)\b", re.I)
_IMPACT_RE = re.compile(r"\b(impact of|effect of|consequence of|caused by|leads to|affects)\b", re.I)
_CAUSAL_RE = re.compile(r"\b(why did|what caused|root cause|triggered by|because of)\b", re.I)


@dataclass
class KGQueryResult:
    strategy_used: str
    facts: list[dict[str, Any]] = field(default_factory=list)
    entities_found: list[str] = field(default_factory=list)
    confidence: float = 0.0


class KGQueryEngine:
    def __init__(self, kg_store: "KnowledgeGraphStore | None" = None) -> None:
        self._kg = kg_store

    def select_strategy(self, query: str) -> str:
        if _RELATIONSHIP_RE.search(query): return "entity"
        if _DEPENDENCY_RE.search(query): return "path"
        if _IMPACT_RE.search(query) or _CAUSAL_RE.search(query): return "impact"
        if re.match(r"^(list|get|fetch|show|count|find)\b", query.strip(), re.I): return "none"
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
        nodes = self._kg.query_nodes(tenant_id=tenant_id, search=query[:100], limit=10)
        facts = [{"entity": n.name, "type": str(n.node_type), "confidence": getattr(n, "confidence", 0.7)}
                 for n in (nodes or [])]
        return KGQueryResult(strategy_used="entity", facts=facts,
                              entities_found=[n.name for n in (nodes or [])],
                              confidence=0.7 if facts else 0.0)

    async def _path_traversal(self, query: str, tenant_id: str) -> KGQueryResult:
        nodes = self._kg.query_nodes(tenant_id=tenant_id, search=query[:100], limit=5)
        facts = [{"from": n.name, "relation": "relates_to", "to": "?"} for n in (nodes or [])[:3]]
        return KGQueryResult(strategy_used="path", facts=facts, confidence=0.65 if facts else 0.0)

    async def _neighbourhood(self, query: str, tenant_id: str, strategy: str) -> KGQueryResult:
        nodes = self._kg.query_nodes(tenant_id=tenant_id, search=query[:100], limit=8)
        facts = [{"entity": n.name, "strategy": strategy} for n in (nodes or [])]
        return KGQueryResult(strategy_used=strategy, facts=facts, confidence=0.6 if facts else 0.0)
