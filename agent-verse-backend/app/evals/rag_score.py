"""RAGScorer — scores retrieval quality from RetrievalResult."""
from __future__ import annotations
from app.rag.agentic.retriever_tool import RetrievalResult


class RAGScorer:
    def score(self, retrieval: RetrievalResult | None) -> float:
        if retrieval is None:
            return 0.5
        if retrieval.source == "none_available":
            return 0.1
        if retrieval.source == "parametric":
            return 0.3
        if retrieval.source == "web":
            return 0.6 + (retrieval.confidence * 0.2)
        if retrieval.source == "knowledge_base":
            return min(1.0, 0.5 + retrieval.confidence * 0.5)
        if retrieval.source == "memory":
            return 0.5
        return max(0.0, min(1.0, retrieval.confidence))
