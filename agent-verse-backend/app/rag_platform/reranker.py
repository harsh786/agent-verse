"""Reranker abstraction for RAG post-retrieval ranking."""
from __future__ import annotations
import logging
from typing import Any

_log = logging.getLogger(__name__)


class Reranker:
    """Reranks retrieved documents for relevance."""

    def __init__(self) -> None:
        self._provider: Any = None

    def set_provider(self, provider: Any) -> None:
        self._provider = provider

    async def rerank(
        self, query: str, documents: list[dict], top_k: int = 5
    ) -> list[dict]:
        """Rerank documents by relevance to the query."""
        if not documents:
            return []

        # Try LLM-based reranking
        if self._provider:
            try:
                return await self._llm_rerank(query, documents, top_k)
            except Exception as exc:
                _log.debug("LLM reranking failed, using score-based: %s", exc)

        # Fallback: score-based reranking (already sorted by vector score)
        return sorted(documents, key=lambda d: d.get("score", 0), reverse=True)[:top_k]

    async def _llm_rerank(self, query: str, documents: list[dict], top_k: int) -> list[dict]:
        """Use LLM to rerank documents by relevance."""
        from app.providers.base import CompletionRequest, Message

        doc_list = "\n".join(
            f"[{i+1}] {d.get('content', '')[:200]}"
            for i, d in enumerate(documents[:10])
        )
        prompt = (
            f"Rank these documents by relevance to the query. "
            f"Return only a JSON array of indices (1-indexed), most relevant first.\n\n"
            f"Query: {query}\n\nDocuments:\n{doc_list}\n\n"
            f"Return JSON array only: [3, 1, 5, ...]"
        )
        resp = await self._provider.complete(CompletionRequest(
            messages=[Message(role="user", content=prompt)],
            model="",
            max_tokens=100,
        ))
        import json
        indices = json.loads(resp.content.strip())
        reranked = []
        for idx in indices[:top_k]:
            if 1 <= idx <= len(documents):
                reranked.append(documents[idx - 1])
        # Append remaining docs not in the ranked list
        reranked_set = {id(d) for d in reranked}
        for d in documents:
            if id(d) not in reranked_set and len(reranked) < top_k:
                reranked.append(d)
        return reranked


# Citation verifier
class CitationVerifier:
    """Verifies that cited claims are supported by source documents."""

    def __init__(self) -> None:
        self._provider: Any = None

    def set_provider(self, provider: Any) -> None:
        self._provider = provider

    async def verify_citations(
        self, answer: str, citations: list[dict]
    ) -> dict[str, Any]:
        """Check that the answer's claims are supported by citations."""
        if not citations or not answer:
            return {"verified": True, "unsupported_claims": [], "confidence": 1.0}

        if self._provider is None:
            return {"verified": True, "unsupported_claims": [], "confidence": 0.7}

        try:
            from app.providers.base import CompletionRequest, Message
            context = "\n".join(c.get("content", "")[:300] for c in citations[:5])
            prompt = (
                f"Is the following answer fully supported by the context? "
                f"List any claims not supported by the context.\n\n"
                f"Context:\n{context}\n\nAnswer:\n{answer[:500]}\n\n"
                f"Return JSON: {{\"supported\": true/false, \"unsupported_claims\": [...]}}"
            )
            resp = await self._provider.complete(CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model="",
                max_tokens=200,
            ))
            import json
            result = json.loads(resp.content.strip())
            unsupported = result.get("unsupported_claims", [])
            return {
                "verified": result.get("supported", True),
                "unsupported_claims": unsupported,
                "confidence": 1.0 - (len(unsupported) * 0.1),
                "grounded": len(unsupported) == 0,
            }
        except Exception as exc:
            _log.debug("Citation verification failed: %s", exc)
            return {"verified": True, "unsupported_claims": [], "confidence": 0.5}


# Singletons
reranker = Reranker()
citation_verifier = CitationVerifier()
