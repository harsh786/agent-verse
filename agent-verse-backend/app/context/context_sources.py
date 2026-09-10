"""Adapters bridging real backend stores to the duck-typed producer
interfaces ``ContextPipeline`` expects for ``graph_facts`` /
``semantic_cache_hits`` (BK3, D-20 follow-up).

``ContextPipeline`` itself never imports a concrete store — it stays generic
and accepts any object exposing the small ``get_facts`` / ``get_hits``
contract via its ``graph_source`` / ``semantic_cache`` constructor params.
Callers that already hold a real dependency (e.g. ``PlannerMixin``, which is
injected with ``KnowledgeGraphStore`` / ``SemanticCache`` through the
existing two-phase app.state wiring) wrap it in the matching adapter here
before constructing the pipeline. This keeps the pipeline decoupled while
still letting the real wiring live close to the backend types it adapts.
"""

from __future__ import annotations

from typing import Any


class KnowledgeGraphFactsSource:
    """Adapts a ``KnowledgeGraphStore`` (``app.knowledge_graph.store``) to the
    ``ContextPipeline`` ``graph_source`` contract:
    ``get_facts(query, tenant_id=None, top_k=5) -> list[dict]``.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    def get_facts(
        self, query: str, tenant_id: str | None = None, top_k: int = 5
    ) -> list[dict[str, Any]]:
        if not tenant_id:
            return []
        nodes = self._store.query_nodes(tenant_id=tenant_id, search=query, limit=top_k)
        facts: list[dict[str, Any]] = []
        for node in nodes or []:
            content = getattr(node, "content", "") or getattr(node, "label", "")
            if not content:
                continue
            facts.append(
                {
                    "fact": content,
                    "node_id": getattr(node, "node_id", None),
                    "confidence": getattr(node, "confidence", None),
                }
            )
        return facts


class SemanticCacheHitsSource:
    """Adapts a ``SemanticCache`` (``app.rag.semantic_cache``) to the
    ``ContextPipeline`` ``semantic_cache`` contract:
    ``get_hits(query, tenant_id=None, top_k=2) -> list[dict]``.

    Uses the cache's synchronous, embedding-free text-key lookup
    (``lookup_text``) since ``ContextPipeline.run`` is synchronous and has no
    embedder / running event loop available to compute a real similarity
    vector.
    """

    def __init__(self, cache: Any) -> None:
        self._cache = cache

    def get_hits(
        self, query: str, tenant_id: str | None = None, top_k: int = 2
    ) -> list[dict[str, Any]]:
        if not tenant_id:
            return []
        cached = self._cache.lookup_text(query, tenant_id)
        return [{"content": cached}] if cached else []
