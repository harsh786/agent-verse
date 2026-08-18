"""Entity and relationship extraction from text."""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from app.knowledge_graph.models import EdgeType, GraphEdge, GraphNode, NodeType

_log = logging.getLogger(__name__)

# Simple entity patterns for deterministic extraction
_ENTITY_PATTERNS = [
    (r'\b([A-Z][a-z]+ [A-Z][a-z]+)\b', 'person'),        # Proper nouns (names)
    (r'\b([A-Z]{2,})\b', 'acronym'),                       # Acronyms
    (r'`([^`]+)`', 'code'),                                 # Code/technical terms
    (r'"([^"]+)"', 'quoted_term'),                          # Quoted terms
    (r'\b(https?://[^\s]+)\b', 'url'),                      # URLs
]


class EntityExtractor:
    """Extract entities and relationships from text."""

    def __init__(self) -> None:
        self._provider: Any = None

    def set_provider(self, provider: Any) -> None:
        self._provider = provider

    def extract_entities_deterministic(
        self, text: str, tenant_id: str, source_id: str | None = None
    ) -> list[GraphNode]:
        """Extract entities using deterministic pattern matching."""
        nodes = []
        seen = set()

        import datetime
        now = datetime.datetime.now(datetime.UTC).isoformat()

        for pattern, entity_type in _ENTITY_PATTERNS:
            for match in re.finditer(pattern, text):
                label = match.group(1) if match.lastindex else match.group(0)
                label = label.strip()[:100]

                if len(label) < 3 or label in seen:
                    continue
                seen.add(label)

                nodes.append(GraphNode(
                    node_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{tenant_id}:{label}")),
                    tenant_id=tenant_id,
                    node_type=NodeType.ENTITY,
                    label=label,
                    content=f"{entity_type}: {label}",
                    source_id=source_id,
                    confidence=0.7,
                    metadata={"entity_type": entity_type, "extraction_method": "deterministic"},
                    created_at=now,
                    updated_at=now,
                ))

        return nodes[:20]  # Cap at 20 entities per text

    async def extract_entities_llm(
        self, text: str, tenant_id: str, source_id: str | None = None
    ) -> list[GraphNode]:
        """Extract entities using LLM for higher quality."""
        from opentelemetry import trace as _trace
        _tracer = _trace.get_tracer(__name__)
        with _tracer.start_as_current_span("knowledge_graph.extract_entities_llm") as span:
            span.set_attribute("tenant_id", tenant_id)
            span.set_attribute("text_len", len(text))
        if self._provider is None:
            return self.extract_entities_deterministic(text, tenant_id, source_id)

        try:
            from app.providers.base import CompletionRequest, Message
            prompt = (
                f"Extract named entities from this text. Return JSON array only:\n"
                f"[{{\"label\": \"entity name\", \"type\": \"person|org|concept|tool|location\", "
                f"\"confidence\": 0.0-1.0}}]\n\n"
                f"Text: {text[:1000]}"
            )
            resp = await self._provider.complete(CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model="",
                max_tokens=500,
            ))

            import json
            entities = json.loads(resp.content.strip())

            import datetime
            now = datetime.datetime.now(datetime.UTC).isoformat()
            nodes = []
            for e in entities[:20]:
                label = str(e.get("label", ""))[:100]
                if len(label) < 2:
                    continue
                nodes.append(GraphNode(
                    node_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{tenant_id}:{label}")),
                    tenant_id=tenant_id,
                    node_type=NodeType.ENTITY,
                    label=label,
                    content=f"{e.get('type', 'entity')}: {label}",
                    source_id=source_id,
                    confidence=float(e.get("confidence", 0.8)),
                    metadata={"entity_type": e.get("type", "unknown"), "extraction_method": "llm"},
                    created_at=now,
                    updated_at=now,
                ))
            return nodes
        except Exception as exc:
            _log.warning("LLM entity extraction failed: %s", exc)
            return self.extract_entities_deterministic(text, tenant_id, source_id)

    async def extract_relationships_llm(
        self, text: str, entities: list[GraphNode], tenant_id: str, source_id: str | None = None
    ) -> list[GraphEdge]:
        """Extract relationships between entities using LLM."""
        if self._provider is None or len(entities) < 2:
            return []

        try:
            from app.providers.base import CompletionRequest, Message
            entity_names = [e.label for e in entities[:10]]
            prompt = (
                f"Find relationships between these entities in the text.\n"
                f"Entities: {entity_names}\n\n"
                f"Return JSON array only:\n"
                f"[{{\"source\": \"entity1\", \"target\": \"entity2\", "
                f"\"relation\": \"mentions|supports|depends_on|references\", "
                f"\"evidence\": \"quote from text\", \"confidence\": 0.0-1.0}}]\n\n"
                f"Text: {text[:800]}"
            )
            resp = await self._provider.complete(CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model="",
                max_tokens=500,
            ))

            import datetime
            import json
            now = datetime.datetime.now(datetime.UTC).isoformat()
            relations = json.loads(resp.content.strip())

            entity_map = {e.label: e for e in entities}
            edges = []
            for r in relations[:20]:
                src_label = r.get("source", "")
                tgt_label = r.get("target", "")
                src_node = entity_map.get(src_label)
                tgt_node = entity_map.get(tgt_label)
                if not src_node or not tgt_node or src_node.node_id == tgt_node.node_id:
                    continue

                try:
                    edge_type = EdgeType(r.get("relation", "mentions"))
                except ValueError:
                    edge_type = EdgeType.MENTIONS

                edges.append(GraphEdge(
                    edge_id=str(uuid.uuid4()),
                    tenant_id=tenant_id,
                    source_node_id=src_node.node_id,
                    target_node_id=tgt_node.node_id,
                    edge_type=edge_type,
                    label=r.get("relation", "mentions"),
                    confidence=float(r.get("confidence", 0.7)),
                    evidence=str(r.get("evidence", ""))[:200],
                    provenance=source_id or "",
                    created_at=now,
                ))
            return edges
        except Exception as exc:
            _log.warning("LLM relationship extraction failed: %s", exc)
            return []


# Module-level singleton
entity_extractor = EntityExtractor()
