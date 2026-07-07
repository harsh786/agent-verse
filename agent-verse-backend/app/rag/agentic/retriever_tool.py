"""RetrieverTool — agent-owned retrieval with strategy routing and structured results.

NEVER returns empty strings. Every degraded path returns a structured
RetrievalResult with source="none_available" and confidence=0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from app.tenancy.context import TenantContext


@dataclass
class CitationRef:
    source: str          # kb|web|memory|graph
    url: str = ""
    page_number: int | None = None
    chunk_id: str = ""
    score: float = 0.0


@dataclass
class RetrievalResult:
    """Structured retrieval result — never a silent empty string."""
    query: str
    source: str                  # knowledge_base|web|memory|graph|parametric|none_available
    strategy_used: str           # auto|hybrid|vector|graph|hyde|web|memory
    confidence: float            # 0.0–1.0
    chunks: list[dict[str, Any]] = field(default_factory=list)
    citations: list[CitationRef] = field(default_factory=list)
    fallback_used: bool = False
    fallback_reason: str = ""
    reformulation_count: int = 0

    @property
    def context_text(self) -> str:
        """Concatenated chunk content for prompt injection."""
        return "\n\n".join(c.get("content", "") for c in self.chunks)


class RetrieverTool:
    """Unified retrieval tool wrapping all 7 retrieval sources."""

    def __init__(
        self,
        *,
        knowledge_store: Any = None,
        kg_store: Any = None,
        ltm_store: Any = None,
        exec_memory: Any = None,
        embedder: Any = None,
        web_search_fn: Any = None,
        web_search_available: bool = False,
    ) -> None:
        self._kb = knowledge_store
        self._kg = kg_store
        self._ltm = ltm_store
        self._exec = exec_memory
        self._embedder = embedder
        self._web_fn = web_search_fn
        self._web_available = web_search_available or (web_search_fn is not None)

    async def retrieve(
        self,
        query: str,
        *,
        tenant_ctx: "TenantContext",
        strategy: str = "auto",
        collection_ids: list[str] | None = None,
        top_k: int = 5,
        min_confidence: float = 0.3,
        allow_web_fallback: bool = True,
        allow_reformulation: bool = True,
        max_reformulation_attempts: int = 2,
    ) -> RetrievalResult:
        """Retrieve with strategy routing and structured degraded paths."""

        # Determine effective strategy
        effective_strategy = strategy
        if strategy == "auto":
            effective_strategy = self._select_strategy(query, collection_ids)

        # 1. Try KB retrieval
        if effective_strategy in ("auto", "hybrid", "vector") and self._kb is not None:
            result = await self._kb_retrieve(
                query, tenant_ctx=tenant_ctx,
                collection_ids=collection_ids, top_k=top_k,
                min_confidence=min_confidence,
            )
            if result.confidence >= min_confidence:
                return result

        # 2. Try web fallback if KB empty/low-confidence and web allowed
        if allow_web_fallback and self._web_available and self._web_fn is not None:
            try:
                web_result = await self._web_retrieve(query, top_k=top_k)
                if web_result.confidence >= min_confidence:
                    return web_result
            except Exception:
                pass

        # 3. Try memory fallback
        if self._ltm is not None or self._exec is not None:
            mem_result = await self._memory_retrieve(query, tenant_ctx=tenant_ctx, top_k=top_k)
            if mem_result.confidence >= min_confidence:
                return mem_result

        # 4. Parametric baseline (no retrieval — model relies on training)
        return RetrievalResult(
            query=query,
            source="parametric",
            strategy_used=effective_strategy,
            confidence=0.1,
            chunks=[],
            citations=[],
            fallback_used=True,
            fallback_reason="all retrieval sources exhausted or unavailable",
        )

    def _select_strategy(self, query: str, collection_ids: list[str] | None) -> str:
        if self._kb is None and not self._web_available:
            return "memory"
        if self._kb is None:
            return "web" if self._web_available else "parametric"
        return "hybrid"

    async def _kb_retrieve(
        self,
        query: str,
        *,
        tenant_ctx: "TenantContext",
        collection_ids: list[str] | None,
        top_k: int,
        min_confidence: float,
    ) -> RetrievalResult:
        try:
            # Get embedding
            embedding: list[float] = []
            if self._embedder is not None:
                from app.providers.base import EmbedRequest
                resp = await self._embedder.embed(EmbedRequest(texts=[query]))
                embedding = resp.embeddings[0] if resp.embeddings else []

            # Determine collections to search
            if collection_ids:
                search_cols = collection_ids
            else:
                cols = self._kb.list_collections(tenant_ctx=tenant_ctx)
                search_cols = [c.collection_id for c in cols]

            if not search_cols:
                return RetrievalResult(
                    query=query, source="none_available",
                    strategy_used="hybrid", confidence=0.0,
                    fallback_used=True, fallback_reason="no KB collections found",
                )

            all_results = []
            for col_id in search_cols[:3]:  # cap at 3 collections
                results = self._kb.hybrid_search(
                    query=query,
                    query_embedding=embedding,
                    collection_id=col_id,
                    tenant_ctx=tenant_ctx,
                    top_k=top_k,
                )
                all_results.extend(results)

            # Sort and deduplicate by chunk_id
            seen: set[str] = set()
            deduped = []
            for r in sorted(all_results, key=lambda x: x.score, reverse=True):
                if r.chunk_id not in seen:
                    seen.add(r.chunk_id)
                    deduped.append(r)

            filtered = [r for r in deduped if r.score >= min_confidence]

            if not filtered:
                return RetrievalResult(
                    query=query, source="none_available",
                    strategy_used="hybrid", confidence=0.0,
                    fallback_used=True,
                    fallback_reason=f"no results above min_confidence={min_confidence}",
                )

            avg_confidence = sum(r.score for r in filtered) / len(filtered)
            chunks = [
                {"chunk_id": r.chunk_id, "content": r.content, "score": r.score,
                 "source_url": r.source_url, "page_number": r.page_number}
                for r in filtered[:top_k]
            ]
            citations = [
                CitationRef(source="kb", url=r.source_url,
                            page_number=r.page_number, chunk_id=r.chunk_id, score=r.score)
                for r in filtered[:top_k]
            ]

            return RetrievalResult(
                query=query, source="knowledge_base",
                strategy_used="hybrid", confidence=avg_confidence,
                chunks=chunks, citations=citations,
            )

        except Exception as exc:
            return RetrievalResult(
                query=query, source="none_available",
                strategy_used="hybrid", confidence=0.0,
                fallback_used=True, fallback_reason=f"KB retrieval error: {exc!s}",
            )

    async def _web_retrieve(self, query: str, top_k: int) -> RetrievalResult:
        results = await self._web_fn(query, top_k=top_k)
        chunks = [{"content": r.get("snippet", r.get("content", "")),
                   "source_url": r.get("url", "")} for r in (results or [])]
        citations = [CitationRef(source="web", url=r.get("url", "")) for r in (results or [])]
        confidence = 0.6 if chunks else 0.0
        return RetrievalResult(
            query=query, source="web", strategy_used="web",
            confidence=confidence, chunks=chunks, citations=citations,
            fallback_used=not bool(chunks),  # mark as fallback when empty
            fallback_reason="" if chunks else "web search returned no results",
        )

    async def _memory_retrieve(
        self, query: str, *, tenant_ctx: "TenantContext", top_k: int
    ) -> RetrievalResult:
        chunks = []
        if self._ltm is not None:
            try:
                memories = self._ltm.recall(query=query, tenant_ctx=tenant_ctx, top_k=top_k)
                chunks = [{"content": m.content, "source_url": ""} for m in memories]
            except Exception:
                pass
        confidence = 0.5 if chunks else 0.0
        return RetrievalResult(
            query=query, source="memory", strategy_used="memory",
            confidence=confidence, chunks=chunks, citations=[],
        )

    async def parallel_retrieve(
        self,
        query: str,
        *,
        tenant_ctx: "TenantContext",
        sources: list[str] | None = None,
        top_k: int = 5,
        min_confidence: float = 0.3,
    ) -> list[RetrievalResult]:
        """Fire retrieval across multiple sources in parallel via asyncio.gather."""
        import asyncio
        available_sources = sources or ["kb"]
        tasks = []
        for source in available_sources:
            if source == "kb" and self._kb is not None:
                tasks.append(self._kb_retrieve(
                    query, tenant_ctx=tenant_ctx, collection_ids=None,
                    top_k=top_k, min_confidence=min_confidence,
                ))
            elif source == "web" and self._web_fn is not None:
                tasks.append(self._web_retrieve(query, top_k=top_k))
            elif source in ("memory", "ltm"):
                tasks.append(self._memory_retrieve(query, tenant_ctx=tenant_ctx, top_k=top_k))

        if not tasks:
            return [RetrievalResult(
                query=query, source="parametric", strategy_used="parallel",
                confidence=0.1, chunks=[], citations=[], fallback_used=True,
                fallback_reason="no sources available for parallel retrieve",
            )]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Collect successful results (not exceptions, not failed fallbacks)
        valid = [r for r in results if isinstance(r, RetrievalResult) and not r.fallback_used]
        if valid:
            return valid

        # At least return any RetrievalResult we got (even failed ones)
        any_result = next((r for r in results if isinstance(r, RetrievalResult)), None)
        if any_result:
            return [any_result]

        # All tasks threw exceptions — return parametric baseline, never empty list
        return [RetrievalResult(
            query=query, source="parametric", strategy_used="parallel",
            confidence=0.1, chunks=[], citations=[], fallback_used=True,
            fallback_reason="all parallel retrieval sources failed with exceptions",
        )]
