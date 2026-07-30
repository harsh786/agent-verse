"""RetrievalPolicy — selects retrieval strategy based on query + source availability."""
from __future__ import annotations

from enum import StrEnum


class RetrievalStrategy(StrEnum):
    HYBRID = "hybrid"
    GRAPH = "graph"
    HYDE = "hyde"
    WEB = "web"
    MEMORY = "memory"
    PARAMETRIC = "parametric"
    AUTO = "auto"


class RetrievalPolicy:
    def select(
        self,
        query_type: str = "factual",
        kb_available: bool = True,
        web_available: bool = False,
        kg_available: bool = False,
    ) -> RetrievalStrategy:
        if not kb_available and not web_available:
            return RetrievalStrategy.PARAMETRIC
        if not kb_available and web_available:
            return RetrievalStrategy.WEB
        if kg_available and query_type in ("relationship", "impact", "dependency", "causal"):
            return RetrievalStrategy.GRAPH
        return RetrievalStrategy.HYBRID
