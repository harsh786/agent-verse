"""WS-10 item 3: the default RAG path must surface a *calibrated* aggregate
retrieval confidence on the result, flag low-confidence retrievals, and take a
real widening fallback when confidence is low.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.rag.contracts import RAGExecutionRequest, RAGStrategy
from app.rag.engine import RetrievalResult
from app.rag.gateway import (
    RetrievalExecutionContext,
    RetrievalRuntimeDependencies,
    _canonical_result,
    execute_core_strategy,
)
from app.tenancy.context import TenantContext


def _req() -> RAGExecutionRequest:
    return RAGExecutionRequest(
        tenant_id="t1", query="q", requested_strategy_id="hybrid", collection_id="c1", top_k=3
    )


def _r(chunk_id: str, score: float) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id, content=f"c {chunk_id}", score=score, source_metadata={"source": "d"}
    )


def test_result_exposes_calibrated_confidence_for_strong_retrieval() -> None:
    out = _canonical_result(_req(), RAGStrategy.HYBRID, [_r("a", 0.95), _r("b", 0.9)], [])
    assert 0.0 <= out.retrieval_confidence <= 1.0
    # Strong scores → high confidence, not flagged low.
    assert out.retrieval_confidence > 0.5
    assert out.low_confidence is False


def test_result_flags_low_confidence_for_weak_retrieval() -> None:
    out = _canonical_result(_req(), RAGStrategy.HYBRID, [_r("a", 0.05), _r("b", 0.03)], [])
    # Weak absolute scores → low calibrated confidence + flag set.
    assert out.retrieval_confidence < 0.5
    assert out.low_confidence is True


def test_empty_retrieval_is_zero_confidence() -> None:
    out = _canonical_result(_req(), RAGStrategy.HYBRID, [], [])
    assert out.retrieval_confidence == 0.0


# ── real widening fallback on the default gateway path ────────────────────────


def _ctx() -> RetrievalExecutionContext:
    class _Embedder:
        async def embed(self, request: Any) -> Any:
            return SimpleNamespace(embeddings=[[1.0, 0.0] for _ in request.texts])

    async def _runner(operation: Any) -> Any:
        return await operation(SimpleNamespace())

    return RetrievalExecutionContext(
        tenant_context=TenantContext(tenant_id="t1", api_key_id="k1", plan="pro"),
        strategy=RAGStrategy.HYBRID,
        filters={},
        dependencies=RetrievalRuntimeDependencies(
            embedder=_Embedder(),
            llm=None,
            graph_capability=None,
            search_capability=None,
            policy_services=(),
            available_strategies=(RAGStrategy.HYBRID,),
        ),
        _db_operation_runner=_runner,
    )


@pytest.mark.asyncio
async def test_low_confidence_triggers_widening_fallback() -> None:
    calls: list[int] = []

    async def _persisted(*a: Any, **k: Any) -> list[RetrievalResult]:
        # Record the top_k requested each call; first (narrow) call is weak.
        calls.append(int(k.get("top_k") or 0))
        return [_r("a", 0.05), _r("b", 0.04)]

    with patch("app.rag.gateway._search_persisted", side_effect=_persisted):
        out = await execute_core_strategy(RAGStrategy.HYBRID, _req(), _ctx())

    # The weak first retrieval must have triggered a second, WIDER retrieval.
    assert len(calls) >= 2
    assert max(calls) > min(calls)
    assert out.low_confidence is True
    # A trace entry records the honest fallback decision.
    actions = {t.action for t in out.strategy_trace}
    assert "low_confidence_fallback" in actions


@pytest.mark.asyncio
async def test_strong_retrieval_skips_fallback() -> None:
    calls: list[int] = []

    async def _persisted(*a: Any, **k: Any) -> list[RetrievalResult]:
        calls.append(1)
        return [_r("a", 0.98), _r("b", 0.95), _r("c", 0.92)]

    with patch("app.rag.gateway._search_persisted", side_effect=_persisted):
        out = await execute_core_strategy(RAGStrategy.HYBRID, _req(), _ctx())

    assert len(calls) == 1  # no widening
    assert out.low_confidence is False
