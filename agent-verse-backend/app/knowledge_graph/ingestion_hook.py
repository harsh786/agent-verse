"""KG Ingestion Hook — auto-extract entities/relations from ingested chunks.

Called as a background task after `IngestionOrchestrator.ingest()` succeeds.
Falls back silently to deterministic extraction when an LLM provider is
unavailable, so the ingestion pipeline is never blocked.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.knowledge_graph.extractor import KGExtractor
    from app.knowledge_graph.store import KGStore
    from app.providers.base import LLMProvider


class KGIngestionHook:
    """Automatically extract entities + relationships from ingested chunks
    and persist them to the knowledge graph store.

    Parameters
    ----------
    extractor : KGExtractor
        Entity/relation extraction engine.
    kg_store : KGStore
        Target knowledge graph store.
    use_llm : bool
        Whether to use the LLM extractor (requires a provider). Falls back
        to deterministic extraction if False or provider is None.
    """

    def __init__(
        self,
        extractor: KGExtractor,
        kg_store: KGStore,
        use_llm: bool = True,
    ) -> None:
        self._extractor = extractor
        self._kg_store = kg_store
        self._use_llm = use_llm

    async def process(
        self,
        chunks: list[str],
        document_id: str,
        tenant_id: str,
        provider: LLMProvider | None = None,
    ) -> dict[str, int]:
        """Extract KG entities/relations from *chunks* and persist them.

        Returns a summary dict: `{"entities": N, "relations": M}`.
        """
        entities_added = 0
        relations_added = 0
        combined_text = " ".join(chunks[:10])  # limit to first 10 chunks for cost

        try:
            if self._use_llm and provider is not None:
                entities = await self._extractor.extract_entities_llm(
                    text=combined_text,
                    provider=provider,
                )
                relations = await self._extractor.extract_relationships_llm(
                    text=combined_text,
                    entities=entities,
                    provider=provider,
                )
            else:
                entities = self._extractor.extract_entities_deterministic(combined_text)
                relations = []

            for entity in entities:
                try:
                    await self._add_entity(entity, document_id, tenant_id)
                    entities_added += 1
                except Exception:
                    pass

            for relation in relations:
                try:
                    await self._add_relation(relation, document_id, tenant_id)
                    relations_added += 1
                except Exception:
                    pass

        except Exception:
            # KG extraction must never block ingestion
            pass

        return {"entities": entities_added, "relations": relations_added}

    async def _add_entity(
        self,
        entity: Any,
        document_id: str,
        tenant_id: str,
    ) -> None:
        """Persist a single entity to the KG store."""
        if hasattr(self._kg_store, "add_node"):
            node = {
                "id": getattr(entity, "id", str(entity)),
                "label": getattr(entity, "label", str(entity)),
                "type": getattr(entity, "type", "ENTITY"),
                "document_id": document_id,
                "tenant_id": tenant_id,
            }
            if asyncio.iscoroutinefunction(self._kg_store.add_node):
                await self._kg_store.add_node(node)
            else:
                self._kg_store.add_node(node)

    async def _add_relation(
        self,
        relation: Any,
        document_id: str,
        tenant_id: str,
    ) -> None:
        """Persist a single relation to the KG store."""
        if hasattr(self._kg_store, "add_edge"):
            edge = {
                "source": getattr(relation, "source", ""),
                "target": getattr(relation, "target", ""),
                "relation": getattr(relation, "relation", "RELATED_TO"),
                "document_id": document_id,
                "tenant_id": tenant_id,
            }
            if asyncio.iscoroutinefunction(self._kg_store.add_edge):
                await self._kg_store.add_edge(edge)
            else:
                self._kg_store.add_edge(edge)
