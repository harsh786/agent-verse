"""RAG Retriever - unified retrieval across vector, graph, and multimodal."""
from __future__ import annotations
import logging
from typing import Any
from app.rag_platform.query_planner import RAGStrategy, RetrievalLeg, RAGResult, QueryPlanner

_log = logging.getLogger(__name__)


class RAGRetriever:
    """Unified retrieval across vector search, graph expansion, and multimodal."""

    def __init__(self) -> None:
        self._provider: Any = None
        self._knowledge_store: Any = None
        self._kg_store: Any = None
        self._planner = QueryPlanner()

    def set_dependencies(
        self,
        provider: Any = None,
        knowledge_store: Any = None,
        kg_store: Any = None,
    ) -> None:
        self._provider = provider
        self._knowledge_store = knowledge_store
        self._kg_store = kg_store

    async def retrieve(
        self,
        query: str,
        tenant_id: str,
        collection_id: str | None = None,
        strategy: RAGStrategy = RAGStrategy.AUTO,
        top_k: int = 5,
    ) -> RAGResult:
        """Retrieve relevant content using the specified strategy."""

        if strategy == RAGStrategy.AUTO:
            strategy = self._planner.select_strategy(query)

        result = RAGResult(query=query, strategy_used=strategy)

        # 1. Base vector retrieval
        vector_leg = await self._vector_search(query, tenant_id, collection_id, top_k)
        result.legs.append(vector_leg)

        # 2. Graph expansion (if GRAPH or MULTI_HOP strategy)
        if strategy in (RAGStrategy.GRAPH, RAGStrategy.MULTI_HOP) and self._kg_store:
            graph_leg = await self._graph_expand(query, tenant_id, vector_leg.results)
            result.legs.append(graph_leg)

        # 3. HyDE (if HYDE strategy)
        if strategy == RAGStrategy.HYDE and self._provider:
            hyde_leg = await self._hyde_retrieve(query, tenant_id, collection_id, top_k)
            result.legs.append(hyde_leg)

        # 4. Synthesize answer with citations
        all_chunks: list[dict] = []
        for leg in result.legs:
            all_chunks.extend(leg.results)

        # Rerank results
        if len(all_chunks) > top_k:
            try:
                from app.rag_platform.reranker import reranker
                if self._provider:
                    reranker.set_provider(self._provider)
                all_chunks = await reranker.rerank(query, all_chunks, top_k)
            except Exception:
                all_chunks = all_chunks[:top_k]

        if all_chunks:
            result.citations = [
                {
                    "index": i + 1,
                    "content": c.get("content", "")[:300],
                    "score": c.get("score", 0.0),
                    "source": c.get("source", ""),
                    "collection_id": c.get("collection_id", ""),
                }
                for i, c in enumerate(all_chunks[:top_k])
            ]
            result.confidence = sum(c.get("score", 0) for c in all_chunks[:top_k]) / max(
                len(all_chunks[:top_k]), 1
            )

        # 5. Generate answer
        result.answer = await self._synthesize(query, all_chunks[:top_k])
        result.grounded = len(result.citations) > 0

        # Verify citations
        if result.answer and result.citations:
            try:
                from app.rag_platform.reranker import citation_verifier
                if self._provider:
                    citation_verifier.set_provider(self._provider)
                verification = await citation_verifier.verify_citations(
                    result.answer, result.citations
                )
                result.grounded = verification.get("grounded", True)
                result.refused_claims = verification.get("unsupported_claims", [])
            except Exception:
                pass

        return result

    async def _vector_search(
        self,
        query: str,
        tenant_id: str,
        collection_id: str | None,
        top_k: int,
    ) -> RetrievalLeg:
        """Perform vector similarity search."""
        import time

        start = time.monotonic()
        results: list[dict] = []

        if self._knowledge_store and hasattr(self._knowledge_store, "search"):
            try:
                search_results = await self._knowledge_store.search(
                    query=query,
                    collection_id=collection_id,
                    tenant_id=tenant_id,
                    top_k=top_k,
                )
                results = search_results if isinstance(search_results, list) else []
            except Exception as exc:
                _log.warning("Vector search failed: %s", exc)

        return RetrievalLeg(
            strategy=RAGStrategy.DIRECT,
            query=query,
            results=results,
            score=sum(r.get("score", 0) for r in results) / max(len(results), 1),
            latency_ms=(time.monotonic() - start) * 1000,
        )

    async def _graph_expand(
        self,
        query: str,
        tenant_id: str,
        seed_results: list[dict],
    ) -> RetrievalLeg:
        """Expand retrieval using the knowledge graph."""
        import time

        start = time.monotonic()
        expanded: list[dict] = []

        if self._kg_store:
            try:
                nodes = self._kg_store.query_nodes(
                    tenant_id,
                    search=query.split()[0] if query else "",
                    limit=5,
                )
                for node in nodes:
                    edges = self._kg_store.get_edges_for_node(node.node_id, tenant_id)
                    for edge in edges[:3]:
                        expanded.append({
                            "content": f"[Graph] {node.label} {edge.edge_type.value} related node",
                            "score": node.confidence * 0.8,
                            "source": "knowledge_graph",
                            "edge_type": edge.edge_type.value,
                        })
            except Exception as exc:
                _log.warning("Graph expansion failed: %s", exc)

        return RetrievalLeg(
            strategy=RAGStrategy.GRAPH,
            query=query,
            results=expanded,
            score=sum(r.get("score", 0) for r in expanded) / max(len(expanded), 1),
            latency_ms=(time.monotonic() - start) * 1000,
        )

    async def _hyde_retrieve(
        self,
        query: str,
        tenant_id: str,
        collection_id: str | None,
        top_k: int,
    ) -> RetrievalLeg:
        """HyDE: generate a hypothetical document, then search for similar real docs."""
        if self._provider is None:
            return RetrievalLeg(strategy=RAGStrategy.HYDE, query=query, results=[])

        try:
            from app.providers.base import CompletionRequest, Message

            resp = await self._provider.complete(
                CompletionRequest(
                    messages=[
                        Message(role="user", content=f"Write a detailed answer to: {query}")
                    ],
                    model="",
                    max_tokens=200,
                )
            )
            hypothetical_doc = resp.content
            return await self._vector_search(hypothetical_doc, tenant_id, collection_id, top_k)
        except Exception as exc:
            _log.warning("HyDE failed: %s", exc)
            return RetrievalLeg(strategy=RAGStrategy.HYDE, query=query, results=[])

    async def _synthesize(self, query: str, chunks: list[dict]) -> str:
        """Synthesize an answer from retrieved chunks."""
        if not chunks:
            return "No relevant information found for this query."

        if self._provider is None:
            return "\n\n".join(c.get("content", "")[:200] for c in chunks[:3])

        try:
            from app.providers.base import CompletionRequest, Message

            context = "\n\n".join(
                f"[{i+1}] {c.get('content', '')[:300]}" for i, c in enumerate(chunks[:5])
            )
            resp = await self._provider.complete(
                CompletionRequest(
                    messages=[
                        Message(
                            role="user",
                            content=(
                                "Answer the question using ONLY the provided context. "
                                "Cite sources using [N] notation. "
                                "If the context doesn't support an answer, say so.\n\n"
                                f"Context:\n{context}\n\nQuestion: {query}"
                            ),
                        )
                    ],
                    model="",
                    max_tokens=500,
                )
            )
            return resp.content
        except Exception as exc:
            _log.warning("Synthesis failed: %s", exc)
            return "\n\n".join(c.get("content", "")[:200] for c in chunks[:3])


# Module-level singleton
rag_retriever = RAGRetriever()
