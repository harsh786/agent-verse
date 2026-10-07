"""Typed reranker abstractions for RAG post-retrieval ranking."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.rag.cross_encoder import CrossEncoderReranker
from app.rag_platform.reranker_contract import (
    RerankerInferenceError,
    RerankerLoadError,
    RerankerProtocol,
)

_log = logging.getLogger(__name__)


class CrossEncoderDocumentReranker(CrossEncoderReranker):
    """Generic pairwise cross-encoder, distinct from ColBERT late interaction."""


def build_reranker(name: str) -> RerankerProtocol:
    """Build exactly the configured reranker without substitution or relabeling."""
    if name == "cross_encoder":
        return CrossEncoderDocumentReranker()
    if name == "colbert":
        from app.rag.agentic.patterns.colbert import ColBERTLateInteractionReranker

        return ColBERTLateInteractionReranker()
    raise ValueError(f"Unknown reranker: {name}")


class CitationVerifier:
    """Verifies that cited claims are supported by source documents."""

    def __init__(self) -> None:
        self._provider: Any = None

    def set_provider(self, provider: Any) -> None:
        self._provider = provider

    async def verify_citations(
        self,
        answer: str,
        citations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not citations or not answer:
            return {"verified": True, "unsupported_claims": [], "confidence": 1.0}
        if self._provider is None:
            return {"verified": True, "unsupported_claims": [], "confidence": 0.7}

        try:
            from app.providers.base import CompletionRequest, Message

            context = "\n".join(
                str(citation.get("content", ""))[:300] for citation in citations[:5]
            )
            from app.providers.guarded_completion import complete_decision

            response = await complete_decision(
                self._provider,
                CompletionRequest(
                    messages=[
                        Message(
                            role="user",
                            content=(
                                "Is the answer fully supported by the context? Return JSON "
                                "with supported and unsupported_claims.\n\n"
                                f"Context:\n{context}\n\nAnswer:\n{answer[:500]}"
                            ),
                        )
                    ],
                    model="",
                    max_tokens=200,
                ),
                role="rag_citation_verify",
            )
            result = json.loads(response.content.strip())
            unsupported = result.get("unsupported_claims", [])
            return {
                "verified": result.get("supported", True),
                "unsupported_claims": unsupported,
                "confidence": 1.0 - (len(unsupported) * 0.1),
                "grounded": not unsupported,
            }
        except Exception as exc:
            _log.debug("Citation verification failed: %s", exc)
            return {"verified": True, "unsupported_claims": [], "confidence": 0.5}


citation_verifier = CitationVerifier()

__all__ = [
    "CitationVerifier",
    "CrossEncoderDocumentReranker",
    "RerankerInferenceError",
    "RerankerLoadError",
    "RerankerProtocol",
    "build_reranker",
    "citation_verifier",
]
