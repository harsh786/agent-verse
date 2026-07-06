"""RAG Query Planner - select optimal retrieval strategy."""
from __future__ import annotations
from enum import Enum
from dataclasses import dataclass, field
from typing import Any


class RAGStrategy(str, Enum):
    DIRECT = "direct"           # Simple vector search
    MULTI_HOP = "multi_hop"    # Multi-turn retrieval
    HYDE = "hyde"               # Hypothetical Document Embeddings
    GRAPH = "graph"             # Graph-expanded retrieval
    MULTIMODAL = "multimodal"  # Search across all modalities
    AUTO = "auto"               # Let the system decide


@dataclass
class RetrievalLeg:
    """A single retrieval attempt with its results."""
    strategy: RAGStrategy
    query: str
    results: list[dict] = field(default_factory=list)
    score: float = 0.0
    latency_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RAGResult:
    """Full RAG retrieval result with all legs and synthesis."""
    query: str
    strategy_used: RAGStrategy
    legs: list[RetrievalLeg] = field(default_factory=list)
    answer: str = ""
    citations: list[dict] = field(default_factory=list)
    grounded: bool = True
    confidence: float = 0.0
    refused_claims: list[str] = field(default_factory=list)


class QueryPlanner:
    """Plans and executes RAG retrieval with multiple strategies."""

    def select_strategy(
        self, query: str, available_modalities: list[str] | None = None
    ) -> RAGStrategy:
        """Auto-select the best strategy for a query."""
        q_lower = query.lower()

        # Multi-hop indicators
        if any(kw in q_lower for kw in ["how does", "why did", "what caused", "explain"]):
            return RAGStrategy.MULTI_HOP

        # Graph indicators
        if any(kw in q_lower for kw in ["related to", "connected to", "depends on", "leads to"]):
            return RAGStrategy.GRAPH

        # Multimodal indicators
        if available_modalities and len(available_modalities) > 1:
            if any(kw in q_lower for kw in ["image", "picture", "video", "chart", "diagram"]):
                return RAGStrategy.MULTIMODAL

        # Default
        return RAGStrategy.DIRECT
