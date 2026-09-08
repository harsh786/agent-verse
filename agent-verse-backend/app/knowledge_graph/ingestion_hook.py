"""KG ingestion hook — extract entities/relations from ingested text.

This is the bridge that makes document ingestion actually feed the knowledge
graph that GraphRAG reads (``app/rag/agentic/patterns/graph.py`` →
``query_graph_evidence`` reads ``knowledge_nodes`` / ``knowledge_edges``).

``extract_and_store_graph`` runs the real :class:`EntityExtractor` over a piece
of text and persists the resulting nodes/edges through
:class:`KnowledgeGraphStore`'s ``add_node`` / ``add_edge`` — which upsert into
the in-memory tenant index *and* best-effort persist to the RLS-scoped DB tables
via ``sqlalchemy_rls_context`` (see ``store._persist_node_to_db``).

Unlike the previous dead implementation, failures in the real store/extractor
logic are **not** swallowed — they surface to the caller so ingestion can log
and decide. (The store's *DB* persistence is intentionally best-effort and
already isolated inside the store.)

TODO(pipeline-wiring): invoke ``extract_and_store_graph`` from the ingestion
pipeline's ENRICH stage (Stage 9) — or immediately after INDEX — in
``app/ingestion/`` once per indexed chunk/document, passing the chunk text,
the tenant id, the chunk/document id as ``source_id``, the shared
``kg_store`` singleton, and the configured LLM provider (or ``None`` for the
deterministic path). That wiring is deliberately out of scope here to avoid
touching ``app/ingestion/*``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.knowledge_graph.extractor import EntityExtractor
    from app.knowledge_graph.store import KnowledgeGraphStore
    from app.providers.base import LLMProvider

_log = logging.getLogger(__name__)


async def extract_and_store_graph(
    text: str,
    tenant_id: str,
    source_id: str,
    *,
    extractor: EntityExtractor,
    store: KnowledgeGraphStore,
    provider: LLMProvider | None = None,
) -> int:
    """Extract entities/relations from *text* and persist them to the KG.

    Parameters
    ----------
    text:
        The chunk/document text to analyse.
    tenant_id:
        Owning tenant — stamped on every node/edge so the store's in-memory
        index and the DB RLS policies keep the data tenant-scoped.
    source_id:
        Provenance id (chunk id / document id) recorded on each node/edge.
    extractor:
        The real :class:`EntityExtractor`. When *provider* is given, its
        higher-quality LLM path is used; otherwise the deterministic regex path.
    store:
        Target :class:`KnowledgeGraphStore`. ``add_node`` / ``add_edge`` upsert
        into the tenant index and best-effort persist to ``knowledge_nodes`` /
        ``knowledge_edges`` under ``sqlalchemy_rls_context``.
    provider:
        Optional LLM provider for entity + relationship extraction. ``None``
        selects the deterministic (regex) extraction path.

    Returns
    -------
    int
        The number of graph elements (nodes + edges) added.

    Notes
    -----
    Errors raised by the extractor or the store surface to the caller — this
    function does **not** wrap real logic in a silent ``except``. Only the DB
    write inside the store is best-effort (by design, so ingestion is never
    blocked by a transient DB issue).
    """
    if not text or not text.strip():
        return 0

    if provider is not None:
        extractor.set_provider(provider)
        nodes = await extractor.extract_entities_llm(text, tenant_id, source_id)
        edges = await extractor.extract_relationships_llm(text, nodes, tenant_id, source_id)
    else:
        nodes = extractor.extract_entities_deterministic(text, tenant_id, source_id)
        edges = []

    added = 0
    for node in nodes:
        store.add_node(node)
        added += 1
    for edge in edges:
        store.add_edge(edge)
        added += 1

    _log.debug(
        "KG ingestion: stored %d nodes + %d edges for tenant=%s source=%s",
        len(nodes),
        len(edges),
        tenant_id,
        source_id,
    )
    return added


class KGIngestionHook:
    """Object adapter the :class:`~app.ingestion.pipeline.IngestionPipeline` calls
    once per indexed document (D-15 wiring).

    ``extract_and_store_graph`` returns a single combined node+edge count, but the
    pipeline records entities and relations separately, so ``process`` runs the
    same real extractor/store path and splits the two counts.

    Holds the shared ``kg_store`` singleton by default — the same object the
    FastAPI lifespan upgrades with a DB session factory (``kg_store.set_db(...)``)
    — so DB persistence follows automatically once wiring completes. Deterministic
    (regex) extraction is the default; passing an LLM ``provider`` to ``process``
    selects the higher-quality LLM path.
    """

    def __init__(self, *, store: object | None = None, extractor: object | None = None) -> None:
        from app.knowledge_graph.extractor import EntityExtractor
        from app.knowledge_graph.store import kg_store

        self._store = store if store is not None else kg_store
        self._extractor = extractor if extractor is not None else EntityExtractor()

    async def process(
        self,
        *,
        chunks: list[str],
        document_id: str,
        tenant_id: str,
        provider: LLMProvider | None = None,
    ) -> dict[str, int]:
        """Extract entities/relations from each chunk and persist them; return counts.

        Errors from the extractor/store surface to the caller (the pipeline wraps
        this call in its own guard so a KG failure never blocks ingestion).
        """
        entities = 0
        relations = 0
        for idx, text in enumerate(chunks):
            if not text or not text.strip():
                continue
            source_id = f"{document_id}:{idx}"
            if provider is not None:
                self._extractor.set_provider(provider)
                nodes = await self._extractor.extract_entities_llm(text, tenant_id, source_id)
                edges = await self._extractor.extract_relationships_llm(
                    text, nodes, tenant_id, source_id
                )
            else:
                nodes = self._extractor.extract_entities_deterministic(text, tenant_id, source_id)
                edges = []
            for node in nodes:
                self._store.add_node(node)
                entities += 1
            for edge in edges:
                self._store.add_edge(edge)
                relations += 1
        return {"entities": entities, "relations": relations}
