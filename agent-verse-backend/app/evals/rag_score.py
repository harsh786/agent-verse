"""RAGScorer — scores retrieval quality from RetrievalResult."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.rag.agentic.retriever_tool import RetrievalResult


class RAGScorer:
    def score(self, retrieval: RetrievalResult | Mapping[str, Any] | None) -> float | None:
        if retrieval is None:
            return None
        source = (
            str(retrieval.get("source", "")) if isinstance(retrieval, Mapping) else retrieval.source
        )
        confidence = (
            float(retrieval.get("confidence", 0.0))
            if isinstance(retrieval, Mapping)
            else retrieval.confidence
        )
        if source == "none_available":
            return 0.1
        if source == "parametric":
            return 0.3
        if source == "web":
            return 0.6 + (confidence * 0.2)
        if source == "knowledge_base":
            return min(1.0, 0.5 + confidence * 0.5)
        if source == "memory":
            return 0.5
        return max(0.0, min(1.0, confidence))
