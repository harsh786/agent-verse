"""Phase 4 — RAG platform unit tests.

Covers:
  app/rag_platform/query_planner.py  — QueryPlanner.select_strategy, RetrievalLeg, RAGResult
  app/rag_platform/reranker_contract.py — BoundedAsyncExecutor, error classes, protocol
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.rag.contracts import RAGStrategy
from app.rag_platform.query_planner import QueryPlanner, RAGResult, RetrievalLeg
from app.rag_platform.reranker_contract import (
    BoundedAsyncExecutor,
    RerankerInferenceError,
    RerankerLoadError,
    RerankerProtocol,
)


# ── RetrievalLeg ──────────────────────────────────────────────────────────────

class TestRetrievalLeg:
    def test_defaults(self) -> None:
        leg = RetrievalLeg(strategy=RAGStrategy.NAIVE, query="test query")
        assert leg.strategy == RAGStrategy.NAIVE
        assert leg.query == "test query"
        assert leg.results == []
        assert leg.score == 0.0
        assert leg.latency_ms == 0.0
        assert leg.metadata == {}

    def test_with_results(self) -> None:
        leg = RetrievalLeg(
            strategy=RAGStrategy.MULTI_HOP,
            query="q",
            results=[{"doc": "a"}, {"doc": "b"}],
            score=0.85,
            latency_ms=120.5,
        )
        assert len(leg.results) == 2
        assert leg.score == 0.85
        assert leg.latency_ms == 120.5


# ── RAGResult ─────────────────────────────────────────────────────────────────

class TestRAGResult:
    def test_defaults(self) -> None:
        result = RAGResult(query="What is RAG?", strategy_used=RAGStrategy.NAIVE)
        assert result.query == "What is RAG?"
        assert result.answer == ""
        assert result.legs == []
        assert result.citations == []
        assert result.grounded is True
        assert result.confidence == 0.0
        assert result.refused_claims == []

    def test_with_answer(self) -> None:
        result = RAGResult(
            query="q",
            strategy_used=RAGStrategy.MULTI_HOP,
            answer="The answer is 42",
            confidence=0.9,
            grounded=True,
        )
        assert result.answer == "The answer is 42"
        assert result.confidence == 0.9


# ── QueryPlanner.select_strategy ─────────────────────────────────────────────

class TestQueryPlannerSelectStrategy:
    @pytest.fixture
    def planner(self) -> QueryPlanner:
        return QueryPlanner()

    # Multi-hop triggers
    def test_how_does_selects_multi_hop(self, planner: QueryPlanner) -> None:
        assert planner.select_strategy("How does the circuit breaker work?") == RAGStrategy.MULTI_HOP

    def test_why_did_selects_multi_hop(self, planner: QueryPlanner) -> None:
        assert planner.select_strategy("Why did the deployment fail?") == RAGStrategy.MULTI_HOP

    def test_what_caused_selects_multi_hop(self, planner: QueryPlanner) -> None:
        assert planner.select_strategy("What caused the latency spike?") == RAGStrategy.MULTI_HOP

    def test_explain_selects_multi_hop(self, planner: QueryPlanner) -> None:
        assert planner.select_strategy("Explain how authentication works") == RAGStrategy.MULTI_HOP

    # Graph triggers
    def test_related_to_selects_graph(self, planner: QueryPlanner) -> None:
        assert planner.select_strategy("What services are related to the auth module?") == RAGStrategy.GRAPH

    def test_connected_to_selects_graph(self, planner: QueryPlanner) -> None:
        assert planner.select_strategy("What is connected to the API gateway?") == RAGStrategy.GRAPH

    def test_depends_on_selects_graph(self, planner: QueryPlanner) -> None:
        assert planner.select_strategy("Which components depends on the DB?") == RAGStrategy.GRAPH

    def test_leads_to_selects_graph(self, planner: QueryPlanner) -> None:
        assert planner.select_strategy("What leads to a 500 error?") == RAGStrategy.GRAPH

    # Default (NAIVE)
    def test_simple_lookup_selects_naive(self, planner: QueryPlanner) -> None:
        assert planner.select_strategy("What is the API key?") == RAGStrategy.NAIVE

    def test_empty_query_selects_naive(self, planner: QueryPlanner) -> None:
        assert planner.select_strategy("") == RAGStrategy.NAIVE

    def test_short_query_selects_naive(self, planner: QueryPlanner) -> None:
        assert planner.select_strategy("List all tenants") == RAGStrategy.NAIVE

    def test_case_insensitive_matching(self, planner: QueryPlanner) -> None:
        assert planner.select_strategy("HOW DOES Redis work?") == RAGStrategy.MULTI_HOP

    def test_available_modalities_param_accepted(self, planner: QueryPlanner) -> None:
        """select_strategy accepts optional modalities list without crashing."""
        result = planner.select_strategy(
            "What is the pricing?",
            available_modalities=["text", "image"],
        )
        assert isinstance(result, RAGStrategy)

    def test_return_type_is_always_rag_strategy(self, planner: QueryPlanner) -> None:
        for query in ["hi", "how", "related", "depends on X", "connected to Y"]:
            result = planner.select_strategy(query)
            assert isinstance(result, RAGStrategy)


# ── RerankerInferenceError / RerankerLoadError ───────────────────────────────

class TestRerankerErrors:
    def test_load_error_is_runtime_error(self) -> None:
        err = RerankerLoadError("model not found")
        assert isinstance(err, RuntimeError)
        assert "model not found" in str(err)

    def test_inference_error_is_runtime_error(self) -> None:
        err = RerankerInferenceError("scoring failed")
        assert isinstance(err, RuntimeError)
        assert "scoring failed" in str(err)

    def test_errors_are_distinct_types(self) -> None:
        assert RerankerLoadError is not RerankerInferenceError


# ── BoundedAsyncExecutor ─────────────────────────────────────────────────────

class TestBoundedAsyncExecutor:
    @pytest.mark.asyncio
    async def test_run_simple_function(self) -> None:
        executor = BoundedAsyncExecutor(
            max_workers=2,
            max_queue_size=5,
            thread_name_prefix="test-",
        )
        try:
            result = await executor.run(lambda x: x * 2, 21)
            assert result == 42
        finally:
            await executor.aclose()

    @pytest.mark.asyncio
    async def test_run_returns_correct_value(self) -> None:
        executor = BoundedAsyncExecutor(
            max_workers=1,
            max_queue_size=0,
            thread_name_prefix="test-",
        )
        try:
            val = await executor.run(sum, [1, 2, 3, 4])
            assert val == 10
        finally:
            await executor.aclose()

    @pytest.mark.asyncio
    async def test_concurrent_tasks_complete(self) -> None:
        executor = BoundedAsyncExecutor(
            max_workers=4,
            max_queue_size=10,
            thread_name_prefix="test-",
        )
        try:
            results = await asyncio.gather(
                executor.run(lambda: 1),
                executor.run(lambda: 2),
                executor.run(lambda: 3),
            )
            assert sorted(results) == [1, 2, 3]
        finally:
            await executor.aclose()

    @pytest.mark.asyncio
    async def test_closed_executor_raises(self) -> None:
        executor = BoundedAsyncExecutor(
            max_workers=1,
            max_queue_size=0,
            thread_name_prefix="test-",
        )
        await executor.aclose()
        with pytest.raises(RuntimeError, match="closed"):
            await executor.run(lambda: 42)

    @pytest.mark.asyncio
    async def test_aclose_idempotent(self) -> None:
        executor = BoundedAsyncExecutor(
            max_workers=1,
            max_queue_size=0,
            thread_name_prefix="test-",
        )
        await executor.aclose()
        await executor.aclose()  # second close should not raise

    def test_invalid_max_workers_raises(self) -> None:
        with pytest.raises(ValueError, match="max_workers"):
            BoundedAsyncExecutor(max_workers=0, max_queue_size=0, thread_name_prefix="t")

    def test_invalid_max_queue_size_raises(self) -> None:
        with pytest.raises(ValueError, match="max_queue_size"):
            BoundedAsyncExecutor(max_workers=1, max_queue_size=-1, thread_name_prefix="t")

    def test_sync_close(self) -> None:
        executor = BoundedAsyncExecutor(
            max_workers=1,
            max_queue_size=0,
            thread_name_prefix="test-",
        )
        executor.close()  # should not raise


# ── RerankerProtocol structural check ────────────────────────────────────────

class TestRerankerProtocol:
    def test_protocol_is_runtime_checkable(self) -> None:
        """RerankerProtocol must be usable with isinstance()."""
        from app.rag_platform.reranker_contract import RerankerProtocol

        class GoodReranker:
            async def score(self, query: str, documents: list[str]) -> list[float]:
                return [1.0] * len(documents)

        class BadReranker:
            def rank(self) -> None:  # wrong method name
                pass

        assert isinstance(GoodReranker(), RerankerProtocol)
        assert not isinstance(BadReranker(), RerankerProtocol)

    def test_async_closeable_protocol_is_runtime_checkable(self) -> None:
        from app.rag_platform.reranker_contract import AsyncCloseableProtocol

        class HasClose:
            async def aclose(self) -> None:
                pass

        class NoClose:
            pass

        assert isinstance(HasClose(), AsyncCloseableProtocol)
        assert not isinstance(NoClose(), AsyncCloseableProtocol)
