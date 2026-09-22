"""Tests for app.knowledge.federated_search.federated_search().

Covers: multi-source success + score normalization, merge/dedup across
sources, and failure propagation. NOTE: this function deliberately does NOT
isolate per-source failures -- a single collection erroring or timing out
propagates and fails the *whole* federated search rather than being silently
dropped. That is intentional, existing, documented behaviour (see the
docstring in app/knowledge/federated_search.py and
tests/rag/test_knowledge_v2.py::test_federated_search_propagates_collection_error,
which a naive "per-source isolation" fix would break -- along with
tests/rag/test_gateway_entrypoints.py::test_gateway_failure_is_sanitized_non_success_for_query_and_chat,
which relies on /knowledge/chat's underlying federated_search call raising
so the API layer can return a sanitized 503). The "partial failure" and
"all sources fail" cases below assert that propagation, not isolation.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.knowledge.federated_search import federated_search
from app.rag.contracts import (
    RAGCitation,
    RAGExecutionResult,
    RAGRetrievalLeg,
    RAGStrategy,
    RAGStrategyTrace,
)
from app.tenancy.context import PlanTier, TenantContext

TENANT = TenantContext("tenant-federated", PlanTier.PROFESSIONAL, "key-federated")


def _result(*, score: float, chunk_id: str, citation_id: str = "c1", content_hash: str | None = None) -> RAGExecutionResult:
    return RAGExecutionResult(
        requested_strategy_id="hybrid",
        resolved_strategy_id=RAGStrategy.HYBRID,
        citations=[
            RAGCitation(
                citation_id=citation_id,
                chunk_id=chunk_id,
                content=f"content for {chunk_id}",
                score=score,
                source=f"source-{chunk_id}",
                metadata={"content_hash": content_hash} if content_hash else {},
            )
        ],
        retrieval_legs=[
            RAGRetrievalLeg(strategy=RAGStrategy.HYBRID, query="q", result_count=1)
        ],
        strategy_trace=[
            RAGStrategyTrace(strategy=RAGStrategy.HYBRID, action="search", status="complete")
        ],
    )


class _ScriptedGateway:
    """Executes a per-collection script: a value, an exception instance/type to raise."""

    def __init__(self, script: dict[str, Any]) -> None:
        self._script = script
        self.calls: list[str] = []

    async def execute(self, tenant_ctx: TenantContext, **kwargs: Any) -> RAGExecutionResult:
        cid = kwargs["collection_id"]
        self.calls.append(cid)
        outcome = self._script[cid]
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, type) and issubclass(outcome, BaseException):
            raise outcome("scripted failure")
        return outcome


@pytest.mark.asyncio
async def test_empty_collection_ids_returns_empty_without_calling_gateway() -> None:
    gateway = _ScriptedGateway({})

    results = await federated_search(
        "policy", [], gateway, tenant_ctx=TENANT,
    )

    assert results == []
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_all_sources_succeed_merges_and_normalizes_scores() -> None:
    gateway = _ScriptedGateway(
        {
            "col-1": _result(score=0.4, chunk_id="a"),
            "col-2": _result(score=0.9, chunk_id="b"),
        }
    )

    results = await federated_search(
        "policy", ["col-1", "col-2"], gateway, tenant_ctx=TENANT, top_k=10,
    )

    assert {r["chunk_id"] for r in results} == {"a", "b"}
    # Each collection had exactly one result, so single-result normalization
    # (normalized_score == 1.0) applies independently per collection.
    assert all(r["normalized_score"] == 1.0 for r in results)
    assert {r["collection_id"] for r in results} == {"col-1", "col-2"}


@pytest.mark.asyncio
async def test_normalizes_scores_within_a_single_collection() -> None:
    class _MultiResultGateway:
        async def execute(self, tenant_ctx: TenantContext, **kwargs: Any) -> RAGExecutionResult:
            return RAGExecutionResult(
                requested_strategy_id="hybrid",
                resolved_strategy_id=RAGStrategy.HYBRID,
                citations=[
                    RAGCitation(
                        citation_id="low", chunk_id="low", content="low relevance",
                        score=0.2, source="doc", metadata={},
                    ),
                    RAGCitation(
                        citation_id="high", chunk_id="high", content="high relevance",
                        score=0.8, source="doc", metadata={},
                    ),
                ],
            )

    results = await federated_search(
        "policy", ["col-1"], _MultiResultGateway(), tenant_ctx=TENANT, top_k=10,
    )

    by_chunk = {r["chunk_id"]: r["normalized_score"] for r in results}
    assert by_chunk["high"] == pytest.approx(1.0)
    assert by_chunk["low"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_one_source_timing_out_fails_the_whole_search() -> None:
    """A slow/timed-out collection is NOT silently dropped: the timeout
    propagates out of federated_search even though other collections would
    have succeeded. This is intentional (see module docstring) so a caller
    never mistakes a partial outage for a complete, trustworthy result set."""
    gateway = _ScriptedGateway(
        {
            "col-timeout": TimeoutError("upstream search timed out"),
            "col-ok-1": _result(score=0.7, chunk_id="ok-1"),
            "col-ok-2": _result(score=0.9, chunk_id="ok-2"),
        }
    )

    with pytest.raises(TimeoutError, match="upstream search timed out"):
        await federated_search(
            "policy",
            ["col-timeout", "col-ok-1", "col-ok-2"],
            gateway,
            tenant_ctx=TENANT,
            top_k=10,
        )


@pytest.mark.asyncio
async def test_one_source_exception_fails_the_whole_search_not_isolated() -> None:
    """A single broken collection propagates its exception rather than being
    isolated -- federated_search has no per-source fault isolation by
    design; a partial/degraded result set must never look like success."""
    gateway = _ScriptedGateway(
        {
            "col-broken": RuntimeError("connection refused"),
            "col-ok": _result(score=0.5, chunk_id="survivor"),
        }
    )

    with pytest.raises(RuntimeError, match="connection refused"):
        await federated_search(
            "policy", ["col-broken", "col-ok"], gateway, tenant_ctx=TENANT, top_k=10,
        )


@pytest.mark.asyncio
async def test_all_sources_failing_propagates_an_exception() -> None:
    gateway = _ScriptedGateway(
        {
            "col-1": RuntimeError("db unavailable"),
            "col-2": TimeoutError("timed out"),
            "col-3": ConnectionError("refused"),
        }
    )

    with pytest.raises((RuntimeError, TimeoutError, ConnectionError)):
        await federated_search(
            "policy", ["col-1", "col-2", "col-3"], gateway, tenant_ctx=TENANT, top_k=10,
        )


@pytest.mark.asyncio
async def test_result_merging_dedup_across_sources_prefers_higher_score() -> None:
    gateway = _ScriptedGateway(
        {
            "col-1": _result(score=0.6, chunk_id="shared", citation_id="cid-1", content_hash="same-hash"),
            "col-2": _result(score=0.95, chunk_id="shared", citation_id="cid-2", content_hash="same-hash"),
        }
    )

    results = await federated_search(
        "policy", ["col-1", "col-2"], gateway, tenant_ctx=TENANT, top_k=10,
    )

    assert len(results) == 1
    merged = results[0]
    assert merged["score"] == 0.95
    assert merged["collection_ids"] == ["col-1", "col-2"]
    assert set(merged["citation_refs"]) == {"col-1:cid-1", "col-2:cid-2"}


@pytest.mark.asyncio
async def test_a_collection_with_zero_matches_is_skipped_without_error() -> None:
    """A collection that legitimately has no matching citations (not an
    error -- just an empty result set) must be skipped when flattening,
    without disrupting results from other collections."""

    gateway = _ScriptedGateway(
        {
            "col-empty": RAGExecutionResult(
                requested_strategy_id="hybrid", resolved_strategy_id=RAGStrategy.HYBRID
            ),
            "col-ok": _result(score=0.5, chunk_id="only-hit"),
        }
    )

    results = await federated_search(
        "policy", ["col-empty", "col-ok"], gateway, tenant_ctx=TENANT, top_k=10,
    )

    assert [r["chunk_id"] for r in results] == ["only-hit"]


@pytest.mark.asyncio
async def test_top_k_truncates_merged_results() -> None:
    gateway = _ScriptedGateway(
        {
            "col-1": _result(score=0.9, chunk_id="a"),
            "col-2": _result(score=0.8, chunk_id="b"),
            "col-3": _result(score=0.7, chunk_id="c"),
        }
    )

    results = await federated_search(
        "policy", ["col-1", "col-2", "col-3"], gateway, tenant_ctx=TENANT, top_k=2,
    )

    assert len(results) == 2


@pytest.mark.asyncio
async def test_per_collection_k_is_forwarded_to_gateway() -> None:
    seen_top_k: list[int] = []

    class _RecordingGateway:
        async def execute(self, tenant_ctx: TenantContext, **kwargs: Any) -> RAGExecutionResult:
            seen_top_k.append(kwargs["top_k"])
            return _result(score=0.5, chunk_id=f"c-{kwargs['collection_id']}")

    await federated_search(
        "policy",
        ["col-1", "col-2"],
        _RecordingGateway(),
        tenant_ctx=TENANT,
        top_k=5,
        per_collection_k=3,
    )

    assert seen_top_k == [3, 3]


@pytest.mark.asyncio
async def test_default_per_collection_k_is_double_top_k() -> None:
    seen_top_k: list[int] = []

    class _RecordingGateway:
        async def execute(self, tenant_ctx: TenantContext, **kwargs: Any) -> RAGExecutionResult:
            seen_top_k.append(kwargs["top_k"])
            return _result(score=0.5, chunk_id=f"c-{kwargs['collection_id']}")

    await federated_search(
        "policy", ["col-1"], _RecordingGateway(), tenant_ctx=TENANT, top_k=4,
    )

    assert seen_top_k == [8]


@pytest.mark.asyncio
async def test_invalid_gateway_return_type_raises_not_silently_dropped() -> None:
    """A gateway returning the wrong type is a programming/contract error,
    distinct from a per-source runtime failure, and must not be swallowed."""

    class _BrokenGateway:
        async def execute(self, tenant_ctx: TenantContext, **kwargs: Any) -> Any:
            return {"not": "a RAGExecutionResult"}

    with pytest.raises(TypeError):
        await federated_search(
            "policy", ["col-1"], _BrokenGateway(), tenant_ctx=TENANT,
        )


@pytest.mark.asyncio
async def test_searches_run_in_parallel_not_sequentially() -> None:
    """Regression guard: collections must be searched concurrently, not one
    at a time, so a single slow source doesn't multiply total latency."""

    async def _slow_execute(tenant_ctx: TenantContext, **kwargs: Any) -> RAGExecutionResult:
        await asyncio.sleep(0.05)
        return _result(score=0.5, chunk_id=f"c-{kwargs['collection_id']}")

    class _SlowGateway:
        execute = staticmethod(_slow_execute)

    start = asyncio.get_event_loop().time()
    await federated_search(
        "policy", ["col-1", "col-2", "col-3"], _SlowGateway(), tenant_ctx=TENANT,
    )
    elapsed = asyncio.get_event_loop().time() - start

    # Sequential execution would take ~0.15s; parallel should stay well under that.
    assert elapsed < 0.12
