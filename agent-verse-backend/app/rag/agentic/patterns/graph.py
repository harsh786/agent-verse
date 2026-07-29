"""Tenant-scoped persisted graph evidence for Graph RAG."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.agentic.patterns.base import RAGPattern, RAGPatternState
from app.rag.engine import RetrievalResult


@dataclass(frozen=True, slots=True)
class GraphEvidenceQuery:
    tenant_id: str
    query: str
    seed_chunk_ids: tuple[str, ...]
    filters: dict[str, Any] = field(default_factory=dict)
    max_per_type: int = 5


@dataclass(frozen=True, slots=True)
class GraphEvidence:
    evidence_id: str
    evidence_type: str
    content: str
    score: float
    provenance: dict[str, Any]


async def query_graph_evidence(
    session: AsyncSession,
    request: GraphEvidenceQuery,
) -> list[GraphEvidence]:
    """Read entity, path, and community evidence in an existing RLS scope."""

    metadata_clause = (
        "AND CAST(node.extra_metadata AS jsonb) @> CAST(:metadata_filter AS jsonb)"
        if request.filters
        else ""
    )
    path_metadata_clause = (
        "AND "
        "CAST(source_node.extra_metadata AS jsonb) @> CAST(:metadata_filter AS jsonb) "
        "AND CAST(target_node.extra_metadata AS jsonb) @> CAST(:metadata_filter AS jsonb) "
        "AND CAST(edge.extra_metadata AS jsonb) @> CAST(:metadata_filter AS jsonb)"
        if request.filters
        else ""
    )
    traversal_edge_metadata_clause = (
        "AND CAST(edge.extra_metadata AS jsonb) @> CAST(:metadata_filter AS jsonb)"
        if request.filters
        else ""
    )
    traversal_node_metadata_clause = (
        "AND CAST(next_node.extra_metadata AS jsonb) @> CAST(:metadata_filter AS jsonb)"
        if request.filters
        else ""
    )
    params: dict[str, Any] = {
        "tenant_id": request.tenant_id,
        "query": request.query[:500],
        "seed_chunk_ids": list(request.seed_chunk_ids),
        "limit": request.max_per_type,
    }
    if request.filters:
        params["metadata_filter"] = json.dumps(request.filters)

    entity_rows = (
        await session.execute(
            text(
                f"""
                /* graph_entity_evidence */
                SELECT node.id, node.label, node.content, node.source_id,
                       node.confidence, node.extra_metadata
                FROM knowledge_nodes AS node
                WHERE node.tenant_id = :tenant_id
                  AND node.node_type IN ('entity', 'concept')
                  AND (
                    node.source_id = ANY(:seed_chunk_ids)
                    OR to_tsvector('english', node.label || ' ' || COALESCE(node.content, ''))
                       @@ plainto_tsquery('english', :query)
                  )
                  {metadata_clause}
                ORDER BY node.confidence DESC, node.id ASC
                LIMIT :limit
                """
            ),
            params,
        )
    ).fetchall()
    path_rows = (
        await session.execute(
            text(
                f"""
                /* graph_path_evidence */
                SELECT edge.id, edge.source_node_id, edge.target_node_id,
                       edge.edge_type, edge.evidence, edge.provenance, edge.confidence
                FROM knowledge_edges AS edge
                JOIN knowledge_nodes AS source_node
                  ON source_node.id = edge.source_node_id
                 AND source_node.tenant_id = :tenant_id
                JOIN knowledge_nodes AS target_node
                  ON target_node.id = edge.target_node_id
                 AND target_node.tenant_id = :tenant_id
                WHERE edge.tenant_id = :tenant_id
                  AND (
                    source_node.source_id = ANY(:seed_chunk_ids)
                    OR target_node.source_id = ANY(:seed_chunk_ids)
                    OR to_tsvector(
                         'english',
                         source_node.label || ' ' || target_node.label || ' '
                         || COALESCE(edge.evidence, '')
                       ) @@ plainto_tsquery('english', :query)
                  )
                  {path_metadata_clause}
                ORDER BY edge.confidence DESC, edge.id ASC
                LIMIT :limit
                """
            ),
            params,
        )
    ).fetchall()
    community_rows = (
        await session.execute(
            text(
                f"""
                /* graph_community_evidence */
                WITH RECURSIVE seed_nodes AS (
                    SELECT node.id
                    FROM knowledge_nodes AS node
                    WHERE node.tenant_id = :tenant_id
                      AND (
                        node.source_id = ANY(:seed_chunk_ids)
                        OR to_tsvector(
                             'english', node.label || ' ' || COALESCE(node.content, '')
                           ) @@ plainto_tsquery('english', :query)
                      )
                      {metadata_clause}
                    ORDER BY node.confidence DESC, node.id ASC
                    LIMIT :limit
                ), community_nodes(root_id, node_id, depth) AS (
                    SELECT seed.id, seed.id, 0 FROM seed_nodes AS seed
                    UNION
                    SELECT community.root_id, next_node.id,
                           community.depth + 1
                    FROM community_nodes AS community
                    JOIN knowledge_edges AS edge
                      ON edge.tenant_id = :tenant_id
                     AND (
                       edge.source_node_id = community.node_id
                       OR edge.target_node_id = community.node_id
                     )
                     {traversal_edge_metadata_clause}
                    JOIN knowledge_nodes AS next_node
                      ON next_node.id = CASE
                           WHEN edge.source_node_id = community.node_id
                           THEN edge.target_node_id
                           ELSE edge.source_node_id
                         END
                     AND next_node.tenant_id = :tenant_id
                     {traversal_node_metadata_clause}
                    WHERE community.depth < 2
                )
                SELECT community.root_id::text AS community_id,
                       string_agg(DISTINCT node.label, ', ' ORDER BY node.label) AS labels,
                       string_agg(DISTINCT NULLIF(node.content, ''), ' ') AS summary,
                       max(node.confidence) AS confidence
                FROM community_nodes AS community
                JOIN knowledge_nodes AS node
                  ON node.id = community.node_id
                 AND node.tenant_id = :tenant_id
                WHERE TRUE
                  {metadata_clause}
                GROUP BY community.root_id
                ORDER BY confidence DESC, community_id ASC
                LIMIT :limit
                """
            ),
            params,
        )
    ).fetchall()

    evidence = [
        GraphEvidence(
            evidence_id=str(row[0]),
            evidence_type="entity",
            content=str(row[2] or row[1]),
            score=float(row[4]),
            provenance={
                "node_id": str(row[0]),
                "label": str(row[1]),
                "source_id": str(row[3] or ""),
                "metadata": dict(row[5] or {}),
            },
        )
        for row in entity_rows
    ]
    evidence.extend(
        GraphEvidence(
            evidence_id=str(row[0]),
            evidence_type="path",
            content=str(row[4] or f"{row[1]} {row[3]} {row[2]}"),
            score=float(row[6]),
            provenance={
                "edge_id": str(row[0]),
                "source_node_id": str(row[1]),
                "target_node_id": str(row[2]),
                "edge_type": str(row[3]),
                "source": str(row[5] or ""),
            },
        )
        for row in path_rows
    )
    evidence.extend(
        GraphEvidence(
            evidence_id=str(row[0]),
            evidence_type="community",
            content=str(row[2] or row[1]),
            score=float(row[3]),
            provenance={"community_id": str(row[0]), "labels": str(row[1] or "")},
        )
        for row in community_rows
    )
    return evidence


def sanitized_tenant_id(tenant_id: str) -> str:
    digest = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


def graph_results(
    evidence: list[GraphEvidence],
    *,
    tenant_id: str,
) -> list[RetrievalResult]:
    stable_tenant_id = sanitized_tenant_id(tenant_id)
    return [
        RetrievalResult(
            chunk_id=f"graph:{item.evidence_type}:{item.evidence_id}",
            content=item.content,
            score=item.score,
            source_metadata={
                "source_type": "graph",
                "source": "knowledge_graph",
                "tenant_id": stable_tenant_id,
                "graph_evidence_type": item.evidence_type,
                "provenance": item.provenance,
            },
            retrieval_legs=[f"graph_{item.evidence_type}"],
            component_scores={f"graph_{item.evidence_type}": item.score},
        )
        for item in evidence
    ]


class GraphRAGPattern(RAGPattern):
    @property
    def pattern_id(self) -> str:
        return "graph_rag"

    @property
    def state(self) -> RAGPatternState:
        return RAGPatternState.IMPLEMENTED

    @property
    def description(self) -> str:
        return "Persisted vector seeds expanded with tenant-scoped graph evidence."

    def is_compatible(self, goal_properties: Any) -> bool:
        return True
