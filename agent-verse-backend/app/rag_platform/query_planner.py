"""RAG Query Planner - select optimal retrieval strategy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.rag.contracts import RAGStrategy


@dataclass
class RetrievalLeg:
    """A single retrieval attempt with its results."""

    strategy: RAGStrategy
    query: str
    results: list[dict[str, Any]] = field(default_factory=list)
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
    citations: list[dict[str, Any]] = field(default_factory=list)
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

        # Default
        return RAGStrategy.NAIVE
