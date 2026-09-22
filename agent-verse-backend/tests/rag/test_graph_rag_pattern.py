"""Fast-tier (no Docker/Postgres) coverage for GraphRAGPattern.

``app/rag/agentic/patterns/graph.py`` had zero test coverage before this
file: ``query_graph_evidence`` (raw tenant-scoped SQL over
``knowledge_nodes``/``knowledge_edges``), ``graph_results`` (evidence ->
``RetrievalResult`` mapping), ``sanitized_tenant_id`` (one-way tenant id for
provenance), and the ``GraphRAGPattern`` metadata class.

This fakes the SQLAlchemy async session protocol (mirroring the pattern used
in ``tests/lifecycle/test_deletion_orchestrator_mocked.py``) so the real
control flow in ``query_graph_evidence`` runs without a real database. The
three queries are distinguished by their SQL comment markers
(``/* graph_entity_evidence */`` etc.), which the fake session inspects to
route to canned rows -- exactly like the queries route through real tables.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.rag.agentic.patterns.base import RAGPatternState
from app.rag.agentic.patterns.graph import (
    GraphEvidence,
    GraphEvidenceQuery,
    GraphRAGPattern,
    graph_results,
    query_graph_evidence,
    sanitized_tenant_id,
)
from app.rag.engine import RetrievalResult, merge_grounding_results


class _Result:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _FakeSession:
    """Fake AsyncSession routing on the SQL comment marker in each query.

    ``rows_by_marker`` maps the marker comment (``graph_entity_evidence``,
    ``graph_path_evidence``, ``graph_community_evidence``) to the fetchall()
    rows that query should return. Anything not present defaults to no rows.
    A marker present in ``raise_for`` raises instead, simulating a graph
    store outage on that leg.
    """

    def __init__(
        self,
        rows_by_marker: dict[str, list[tuple[Any, ...]]] | None = None,
        *,
        raise_for: set[str] | None = None,
        tenant_filter: str | None = None,
    ) -> None:
        self.rows_by_marker = rows_by_marker or {}
        self.raise_for = raise_for or set()
        self.tenant_filter = tenant_filter
        self.seen_params: list[dict[str, Any]] = []

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> _Result:
        sql = str(statement)
        self.seen_params.append(dict(params or {}))
        for marker in ("graph_entity_evidence", "graph_path_evidence", "graph_community_evidence"):
            if marker in sql:
                if marker in self.raise_for:
                    raise RuntimeError(f"graph store unavailable: {marker}")
                rows = self.rows_by_marker.get(marker, [])
                if self.tenant_filter is not None and params is not None:
                    if params.get("tenant_id") != self.tenant_filter:
                        return _Result([])
                return _Result(rows)
        raise AssertionError(f"unrecognized query issued to fake graph session: {sql[:120]}")


def _entity_row(
    node_id: str = "node-1",
    label: str = "Acme Corp",
    content: str = "Acme Corp is a widget maker.",
    source_id: str = "chunk-1",
    confidence: float = 0.9,
    metadata: dict[str, Any] | None = None,
) -> tuple[Any, ...]:
    return (node_id, label, content, source_id, confidence, metadata or {})


def _path_row(
    edge_id: str = "edge-1",
    source_node_id: str = "node-1",
    target_node_id: str = "node-2",
    edge_type: str = "supplies",
    evidence: str = "Acme supplies Widget Co.",
    provenance: str = "doc-1",
    confidence: float = 0.8,
) -> tuple[Any, ...]:
    return (edge_id, source_node_id, target_node_id, edge_type, evidence, provenance, confidence)


def _community_row(
    community_id: str = "community-1",
    labels: str = "Acme Corp, Widget Co",
    summary: str = "A supplier network.",
    confidence: float = 0.7,
    member_node_ids: list[str] | None = None,
    source_document_chunk_ids: list[str] | None = None,
) -> tuple[Any, ...]:
    return (
        community_id,
        labels,
        summary,
        confidence,
        member_node_ids or ["node-1", "node-2"],
        source_document_chunk_ids or ["chunk-1"],
    )


def _request(**overrides: Any) -> GraphEvidenceQuery:
    defaults: dict[str, Any] = {
        "tenant_id": "tenant-a",
        "query": "who supplies widgets",
        "seed_chunk_ids": ("chunk-1",),
    }
    defaults.update(overrides)
    return GraphEvidenceQuery(**defaults)


# ---------------------------------------------------------------------------
# GraphRAGPattern metadata
# ---------------------------------------------------------------------------


class TestGraphRAGPatternMetadata:
    def test_pattern_id(self) -> None:
        pattern = GraphRAGPattern()
        assert pattern.pattern_id == "graph_rag"

    def test_state_is_implemented(self) -> None:
        assert GraphRAGPattern().state is RAGPatternState.IMPLEMENTED

    def test_description_nonempty(self) -> None:
        assert "graph" in GraphRAGPattern().description.lower()

    def test_is_compatible_always_true(self) -> None:
        pattern = GraphRAGPattern()
        assert pattern.is_compatible(None) is True
        assert pattern.is_compatible(object()) is True


# ---------------------------------------------------------------------------
# query_graph_evidence: successful retrieval
# ---------------------------------------------------------------------------


class TestQueryGraphEvidenceSuccess:
    @pytest.mark.asyncio
    async def test_returns_entity_path_and_community_evidence(self) -> None:
        session = _FakeSession(
            {
                "graph_entity_evidence": [_entity_row()],
                "graph_path_evidence": [_path_row()],
                "graph_community_evidence": [_community_row()],
            }
        )
        evidence = await query_graph_evidence(session, _request())  # type: ignore[arg-type]

        types = [item.evidence_type for item in evidence]
        assert types == ["entity", "path", "community"]
        entity = evidence[0]
        assert entity.evidence_id == "node-1"
        assert entity.content == "Acme Corp is a widget maker."
        assert entity.score == pytest.approx(0.9)
        assert entity.provenance["label"] == "Acme Corp"
        assert entity.provenance["source_id"] == "chunk-1"

        path = evidence[1]
        assert path.evidence_type == "path"
        assert path.content == "Acme supplies Widget Co."
        assert path.provenance["edge_type"] == "supplies"

        community = evidence[2]
        assert community.evidence_type == "community"
        assert community.content == "A supplier network."
        assert community.provenance["member_node_ids"] == ["node-1", "node-2"]

    @pytest.mark.asyncio
    async def test_entity_content_falls_back_to_label_when_content_is_none(self) -> None:
        session = _FakeSession(
            {"graph_entity_evidence": [_entity_row(content=None)]}  # type: ignore[arg-type]
        )
        evidence = await query_graph_evidence(session, _request())  # type: ignore[arg-type]
        assert evidence[0].content == "Acme Corp"

    @pytest.mark.asyncio
    async def test_path_content_falls_back_to_labels_when_evidence_is_none(self) -> None:
        session = _FakeSession(
            {"graph_path_evidence": [_path_row(evidence=None)]}  # type: ignore[arg-type]
        )
        evidence = await query_graph_evidence(session, _request())  # type: ignore[arg-type]
        assert evidence[0].content == "node-1 supplies node-2"

    @pytest.mark.asyncio
    async def test_query_is_truncated_to_500_chars_in_params(self) -> None:
        session = _FakeSession()
        long_query = "x" * 900
        await query_graph_evidence(session, _request(query=long_query))  # type: ignore[arg-type]
        assert all(len(p["query"]) <= 500 for p in session.seen_params)

    @pytest.mark.asyncio
    async def test_filters_add_metadata_param_and_clause(self) -> None:
        session = _FakeSession()
        await query_graph_evidence(  # type: ignore[arg-type]
            session, _request(filters={"category": "supplier"})
        )
        assert all("metadata_filter" in p for p in session.seen_params)

    @pytest.mark.asyncio
    async def test_no_filters_omits_metadata_param(self) -> None:
        session = _FakeSession()
        await query_graph_evidence(session, _request())  # type: ignore[arg-type]
        assert all("metadata_filter" not in p for p in session.seen_params)


# ---------------------------------------------------------------------------
# query_graph_evidence: empty graph / no matches
# ---------------------------------------------------------------------------


class TestQueryGraphEvidenceEmpty:
    @pytest.mark.asyncio
    async def test_no_matching_nodes_returns_empty_list(self) -> None:
        session = _FakeSession({})
        evidence = await query_graph_evidence(session, _request())  # type: ignore[arg-type]
        assert evidence == []

    @pytest.mark.asyncio
    async def test_empty_evidence_produces_empty_retrieval_results(self) -> None:
        session = _FakeSession({})
        evidence = await query_graph_evidence(session, _request())  # type: ignore[arg-type]
        results = graph_results(evidence, tenant_id="tenant-a")
        assert results == []


# ---------------------------------------------------------------------------
# query_graph_evidence: graph store unavailable / error
# ---------------------------------------------------------------------------


class TestQueryGraphEvidenceUnavailable:
    @pytest.mark.asyncio
    async def test_entity_query_failure_propagates(self) -> None:
        session = _FakeSession(raise_for={"graph_entity_evidence"})
        with pytest.raises(RuntimeError, match="graph store unavailable"):
            await query_graph_evidence(session, _request())  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_community_query_failure_propagates_even_after_entity_succeeds(self) -> None:
        session = _FakeSession(
            {"graph_entity_evidence": [_entity_row()]},
            raise_for={"graph_community_evidence"},
        )
        with pytest.raises(RuntimeError, match="graph store unavailable"):
            await query_graph_evidence(session, _request())  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_capability_adapter_surfaces_session_failure(self) -> None:
        """The gateway's graph capability adapter wraps query_graph_evidence
        in a DB-operation runner. A raising session should still surface as
        an exception rather than being swallowed."""
        from app.rag.gateway import _BoundTenantScopedGraphCapability

        async def runner(operation: Any) -> Any:
            session = _FakeSession(raise_for={"graph_entity_evidence"})
            return await operation(session)

        capability = _BoundTenantScopedGraphCapability(runner)
        with pytest.raises(RuntimeError, match="graph store unavailable"):
            await capability.retrieve_evidence(_request())


# ---------------------------------------------------------------------------
# graph_results: mapping to RetrievalResult
# ---------------------------------------------------------------------------


class TestGraphResults:
    def test_maps_chunk_id_prefix_and_score(self) -> None:
        evidence = [
            GraphEvidence(
                evidence_id="node-1",
                evidence_type="entity",
                content="Acme Corp",
                score=0.9,
                provenance={"label": "Acme Corp"},
            )
        ]
        results = graph_results(evidence, tenant_id="tenant-a")
        assert len(results) == 1
        result = results[0]
        assert result.chunk_id == "graph:entity:node-1"
        assert result.score == pytest.approx(0.9)
        assert result.source_metadata["source_type"] == "graph"
        assert result.source_metadata["graph_evidence_type"] == "entity"
        assert result.retrieval_legs == ["graph_entity"]
        assert result.component_scores == {"graph_entity": 0.9}

    def test_tenant_id_in_output_is_hashed_not_raw(self) -> None:
        evidence = [
            GraphEvidence("node-1", "entity", "x", 0.5, {}),
        ]
        results = graph_results(evidence, tenant_id="tenant-a-secret")
        assert results[0].source_metadata["tenant_id"] != "tenant-a-secret"
        assert results[0].source_metadata["tenant_id"].startswith("sha256:")

    def test_preserves_order_of_input_evidence(self) -> None:
        evidence = [
            GraphEvidence("e1", "entity", "a", 0.1, {}),
            GraphEvidence("p1", "path", "b", 0.2, {}),
            GraphEvidence("c1", "community", "c", 0.3, {}),
        ]
        results = graph_results(evidence, tenant_id="tenant-a")
        assert [r.chunk_id for r in results] == [
            "graph:entity:e1",
            "graph:path:p1",
            "graph:community:c1",
        ]


# ---------------------------------------------------------------------------
# sanitized_tenant_id
# ---------------------------------------------------------------------------


class TestSanitizedTenantId:
    def test_deterministic_for_same_input(self) -> None:
        assert sanitized_tenant_id("tenant-a") == sanitized_tenant_id("tenant-a")

    def test_differs_for_different_tenants(self) -> None:
        assert sanitized_tenant_id("tenant-a") != sanitized_tenant_id("tenant-b")

    def test_never_contains_raw_tenant_id(self) -> None:
        raw = "tenant-super-secret-uuid"
        assert raw not in sanitized_tenant_id(raw)

    def test_has_stable_prefix(self) -> None:
        assert sanitized_tenant_id("tenant-a").startswith("sha256:")


# ---------------------------------------------------------------------------
# Combining graph results with vector results (merge_grounding_results)
# ---------------------------------------------------------------------------


class TestCombineGraphAndVectorResults:
    def _vector_result(self, chunk_id: str, score: float) -> RetrievalResult:
        return RetrievalResult(
            chunk_id=chunk_id,
            content=f"vector content for {chunk_id}",
            score=score,
            source_metadata={"source_type": "persisted"},
            retrieval_legs=["vector"],
            component_scores={"vector": score},
        )

    def test_disjoint_graph_and_vector_results_are_both_kept(self) -> None:
        vector_seeds = [self._vector_result("chunk-1", 0.6)]
        evidence = [GraphEvidence("node-1", "entity", "Acme Corp", 0.9, {})]
        graph_items = graph_results(evidence, tenant_id="tenant-a")

        merged = merge_grounding_results([vector_seeds, graph_items], top_k=10)

        chunk_ids = {result.chunk_id for result in merged}
        assert chunk_ids == {"chunk-1", "graph:entity:node-1"}

    def test_overlapping_chunk_id_merges_legs_and_keeps_max_score(self) -> None:
        # A graph evidence item can, in principle, resolve to the same
        # chunk_id as a vector seed (e.g. when graph evidence itself cites
        # a persisted chunk); merge_grounding_results should combine them.
        vector_seeds = [self._vector_result("shared-id", 0.4)]
        graph_items = [
            RetrievalResult(
                chunk_id="shared-id",
                content="graph content",
                score=0.95,
                source_metadata={"source_type": "graph"},
                retrieval_legs=["graph_entity"],
                component_scores={"graph_entity": 0.95},
            )
        ]

        merged = merge_grounding_results([vector_seeds, graph_items], top_k=10)

        assert len(merged) == 1
        result = merged[0]
        assert result.score == pytest.approx(0.95)
        assert set(result.retrieval_legs) == {"vector", "graph_entity"}

    def test_top_k_limits_combined_results(self) -> None:
        vector_seeds = [self._vector_result(f"v{i}", 1.0 - i * 0.01) for i in range(5)]
        evidence = [
            GraphEvidence(f"g{i}", "entity", f"content {i}", 0.5 - i * 0.01, {})
            for i in range(5)
        ]
        graph_items = graph_results(evidence, tenant_id="tenant-a")

        merged = merge_grounding_results([vector_seeds, graph_items], top_k=3)
        assert len(merged) == 3


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    @pytest.mark.asyncio
    async def test_tenant_a_query_only_sees_tenant_a_rows(self) -> None:
        """The fake session simulates RLS/tenant filtering the same way the
        real ``WHERE node.tenant_id = :tenant_id`` clause would: rows are
        only returned when the query's tenant_id param matches."""
        session = _FakeSession(
            {"graph_entity_evidence": [_entity_row(node_id="tenant-a-node")]},
            tenant_filter="tenant-a",
        )
        evidence_a = await query_graph_evidence(session, _request(tenant_id="tenant-a"))  # type: ignore[arg-type]
        assert len(evidence_a) == 1

        evidence_b = await query_graph_evidence(session, _request(tenant_id="tenant-b"))  # type: ignore[arg-type]
        assert evidence_b == []

    @pytest.mark.asyncio
    async def test_tenant_id_param_passed_through_unmodified_to_query(self) -> None:
        session = _FakeSession()
        await query_graph_evidence(session, _request(tenant_id="tenant-xyz"))  # type: ignore[arg-type]
        assert all(p["tenant_id"] == "tenant-xyz" for p in session.seen_params)

    def test_graph_results_for_different_tenants_produce_different_stable_ids(self) -> None:
        evidence = [GraphEvidence("node-1", "entity", "Acme Corp", 0.9, {})]
        results_a = graph_results(evidence, tenant_id="tenant-a")
        results_b = graph_results(evidence, tenant_id="tenant-b")
        assert (
            results_a[0].source_metadata["tenant_id"]
            != results_b[0].source_metadata["tenant_id"]
        )

    def test_graph_results_does_not_leak_raw_tenant_id_into_chunk_id_or_content(self) -> None:
        evidence = [GraphEvidence("node-1", "entity", "Acme Corp", 0.9, {})]
        results = graph_results(evidence, tenant_id="tenant-a-secret-uuid")
        for result in results:
            assert "tenant-a-secret-uuid" not in result.chunk_id
            assert "tenant-a-secret-uuid" not in result.content
            assert "tenant-a-secret-uuid" not in str(result.source_metadata)
