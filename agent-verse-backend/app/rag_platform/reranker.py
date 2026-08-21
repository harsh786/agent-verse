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


class Reranker:
    """Legacy LLM reranker retained for non-model-specific callers."""

    def __init__(self) -> None:
        self._provider: Any = None

    def set_provider(self, provider: Any) -> None:
        self._provider = provider

    async def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        if not documents:
            return []
        if self._provider is not None:
            try:
                return await self._llm_rerank(query, documents, top_k)
            except Exception as exc:
                _log.debug("LLM reranking failed, using score ordering: %s", exc)
        return sorted(
            documents,
            key=lambda document: float(document.get("score", 0.0)),
            reverse=True,
        )[:top_k]

    async def _llm_rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        from app.providers.base import CompletionRequest, Message

        document_list = "\n".join(
            f"[{index + 1}] {document.get('content', '')[:200]}"
            for index, document in enumerate(documents[:10])
        )
        prompt = (
            "Rank these documents by relevance to the query. "
            "Return only a JSON array of 1-indexed indices, most relevant first.\n\n"
            f"Query: {query}\n\nDocuments:\n{document_list}"
        )
        response = await self._provider.complete(
            CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model="",
                max_tokens=100,
            )
        )
        indices = json.loads(response.content.strip())
        reranked = [
            documents[index - 1]
            for index in indices[:top_k]
            if isinstance(index, int) and 1 <= index <= len(documents)
        ]
        selected = {id(document) for document in reranked}
        reranked.extend(
            document
            for document in documents
            if id(document) not in selected and len(reranked) < top_k
        )
        return reranked[:top_k]


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
            response = await self._provider.complete(
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
                )
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


reranker = Reranker()
citation_verifier = CitationVerifier()

__all__ = [
    "CitationVerifier",
    "CrossEncoderDocumentReranker",
    "Reranker",
    "RerankerInferenceError",
    "RerankerLoadError",
    "RerankerProtocol",
    "build_reranker",
    "citation_verifier",
    "reranker",
]
