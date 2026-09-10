"""WS-10 item 1: a reranking STAGE must engage on the DEFAULT hybrid retrieval
path (not only on explicit pattern branches), config-gated, and degrade to an
honest passthrough when disabled or when the reranker dependency is unavailable.

These are behavioural tests: they prove the stage reorders results on the
default ``engine.retrieve()`` path via the ONE reranking registry
(``app.context.rerank_policy.RerankPolicy``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.rag import rerank_stage
from app.rag.engine import RetrievalResult


def _settings(enabled: bool = True, strategy: str = "score") -> SimpleNamespace:
    return SimpleNamespace(
        rag_default_rerank_enabled=enabled,
        rag_default_rerank_strategy=strategy,
    )


def _r(chunk_id: str, score: float) -> RetrievalResult:
    return RetrievalResult(
        chunk_id=chunk_id,
        content=f"passage {chunk_id}",
        score=score,
        source_metadata={"source": "doc"},
    )


@pytest.mark.asyncio
async def test_stage_disabled_is_exact_passthrough() -> None:
    results = [_r("c1", 0.2), _r("c2", 0.9), _r("c3", 0.5)]
    out = await rerank_stage.apply_default_rerank(
        results, query="q", query_embedding=None, settings=_settings(enabled=False)
    )
    # Same objects, same order — nothing touched.
    assert out is results or [c.chunk_id for c in out] == ["c1", "c2", "c3"]
    assert [c.chunk_id for c in out] == ["c1", "c2", "c3"]


@pytest.mark.asyncio
async def test_stage_reorders_on_default_path_by_score() -> None:
    # Deliberately unsorted input — a real rerank stage must reorder it.
    results = [_r("c1", 0.2), _r("c2", 0.9), _r("c3", 0.5)]
    out = await rerank_stage.apply_default_rerank(
        results, query="q", query_embedding=None, settings=_settings(strategy="score")
    )
    assert [c.chunk_id for c in out] == ["c2", "c3", "c1"]
    # Additive: no result dropped.
    assert len(out) == 3
    # The stage records which strategy actually ran (observability / honesty).
    assert out[0].source_metadata.get("rerank_strategy") == "score"


@pytest.mark.asyncio
async def test_stage_honest_passthrough_when_reranker_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.context import rerank_policy

    def _boom(self: Any, *a: Any, **k: Any) -> Any:
        raise RuntimeError("reranker backend down")

    monkeypatch.setattr(rerank_policy.RerankPolicy, "rerank", _boom)
    results = [_r("c1", 0.2), _r("c2", 0.9)]
    out = await rerank_stage.apply_default_rerank(
        results, query="q", query_embedding=None, settings=_settings(strategy="score")
    )
    # Degrades cleanly to original order — no exception, no drops.
    assert [c.chunk_id for c in out] == ["c1", "c2"]


@pytest.mark.asyncio
async def test_engine_default_retrieve_engages_rerank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The literal default ``engine.retrieve()`` fallthrough must run the stage."""
    from app.rag import engine

    unsorted_hits = [_r("a", 0.1), _r("b", 0.8), _r("c", 0.4)]

    async def _fake_hybrid(*a: Any, **k: Any) -> list[RetrievalResult]:
        return list(unsorted_hits)

    monkeypatch.setattr(engine, "hybrid_search", _fake_hybrid)
    monkeypatch.setattr(engine, "get_settings", lambda: _settings(strategy="score"))

    out = await engine.retrieve(
        None,  # session unused by the fake hybrid_search
        query="what is x?",
        query_embedding=None,
        collection_id="col-1",
        strategy="hybrid",
    )
    # Proof: default path returned rerank-ordered results, not raw DB order.
    assert [c.chunk_id for c in out] == ["b", "c", "a"]


@pytest.mark.asyncio
async def test_engine_default_retrieve_rerank_off_preserves_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rag import engine

    unsorted_hits = [_r("a", 0.1), _r("b", 0.8), _r("c", 0.4)]

    async def _fake_hybrid(*a: Any, **k: Any) -> list[RetrievalResult]:
        return list(unsorted_hits)

    monkeypatch.setattr(engine, "hybrid_search", _fake_hybrid)
    monkeypatch.setattr(engine, "get_settings", lambda: _settings(enabled=False))

    out = await engine.retrieve(
        None,
        query="what is x?",
        query_embedding=None,
        collection_id="col-1",
        strategy="hybrid",
    )
    # Stage disabled → raw retrieval order preserved.
    assert [c.chunk_id for c in out] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_gateway_hybrid_default_path_reranks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The canonical gateway HYBRID strategy reranks before building citations."""
    from unittest.mock import patch

    from app.providers.base import EmbedRequest, EmbedResponse
    from app.rag.contracts import RAGExecutionRequest, RAGStrategy
    from app.rag.gateway import (
        RetrievalExecutionContext,
        RetrievalRuntimeDependencies,
        execute_core_strategy,
    )
    from app.tenancy.context import TenantContext

    class _Embedder:
        async def embed(self, request: EmbedRequest) -> EmbedResponse:
            return EmbedResponse(embeddings=[[1.0, 0.0] for _ in request.texts])

    async def _runner(operation: Any) -> Any:
        return await operation(SimpleNamespace())

    ctx = RetrievalExecutionContext(
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
    req = RAGExecutionRequest(
        tenant_id="t1",
        query="what is the policy?",
        requested_strategy_id=RAGStrategy.HYBRID.value,
        collection_id="col-1",
        top_k=3,
    )
    # Unsorted persisted hits — a rerank stage on the default path must reorder.
    unsorted = [_r("a", 0.1), _r("b", 0.8), _r("c", 0.4)]

    async def _fake_persisted(*a: Any, **k: Any) -> list[RetrievalResult]:
        return list(unsorted)

    with (
        patch("app.rag.gateway._search_persisted", side_effect=_fake_persisted),
        patch("app.core.config.get_settings", lambda: _settings(strategy="score")),
    ):
        out = await execute_core_strategy(RAGStrategy.HYBRID, req, ctx)

    assert [c.chunk_id for c in out.citations] == ["b", "c", "a"]
    assert out.citations[0].metadata.get("rerank_strategy") == "score"
