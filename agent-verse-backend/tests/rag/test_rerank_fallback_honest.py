"""a04-F073-03: a cross-encoder / hosted rerank that falls back to TF-IDF says so.

An inference error inside the cross-encoder fell back to TF-IDF with only
``last_reason`` set: ``last_strategy_used`` stayed ``cross_encoder``, results
were labelled ``rerank_strategy=cross_encoder`` and nothing was counted. The
hosted reranker's fallback was just as silent.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from app.context.rerank_policy import RerankPolicy, RerankStrategy
from app.observability.metrics import RERANK_DEGRADED_TOTAL
from app.rag.engine import RetrievalResult
from app.rag.rerank_stage import apply_default_rerank


def _count(reason: str) -> float:
    return float(RERANK_DEGRADED_TOTAL.labels(reason=reason)._value.get())


def _boom(query: str, docs: list[str], batch_size: int = 32) -> list[float]:
    raise RuntimeError("CUDA error: device-side assert triggered")


def _chunks() -> list[dict[str, Any]]:
    return [
        {"chunk_id": "other", "content": "weather report", "score": 0.9},
        {"chunk_id": "relevant", "content": "python tutorial", "score": 0.2},
    ]


def test_cross_encoder_error_reports_tfidf_and_is_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.rag.cross_encoder.cross_encode", _boom)
    before = _count("cross_encoder_error")
    policy = RerankPolicy(strategy=RerankStrategy.CROSS_ENCODER, max_per_source=0)

    result = policy.rerank(_chunks(), query="python")

    assert result[0]["chunk_id"] == "relevant"
    assert policy.last_strategy_used is RerankStrategy.TFIDF
    assert policy.last_degraded_reason == "cross_encoder_error"
    assert _count("cross_encoder_error") == before + 1


def test_a_healthy_cross_encoder_is_not_marked_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.rag.cross_encoder.cross_encode",
        lambda q, docs, batch_size=32: [5.0 if "python" in d else -5.0 for d in docs],
    )
    policy = RerankPolicy(strategy=RerankStrategy.CROSS_ENCODER, max_per_source=0)
    policy.rerank(_chunks(), query="python")
    assert policy.last_strategy_used is RerankStrategy.CROSS_ENCODER
    assert policy.last_degraded_reason is None


def _results() -> list[RetrievalResult]:
    return [
        RetrievalResult(chunk_id="other", content="weather report", score=0.9, source_metadata={}),
        RetrievalResult(
            chunk_id="relevant", content="python tutorial", score=0.2, source_metadata={}
        ),
    ]


async def _ready(_settings: Any) -> str:
    return "ready"


async def test_default_path_labels_a_cross_encoder_fallback_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.rag.cross_encoder.cross_encode", _boom)
    monkeypatch.setattr("app.rag.rerank_stage._cross_encoder_status", _ready)
    settings = SimpleNamespace(
        rag_default_rerank_enabled=True, rag_default_rerank_strategy="cross_encoder"
    )
    before = _count("cross_encoder_error")

    out = await apply_default_rerank(
        _results(), query="python", query_embedding=None, settings=settings
    )

    assert out[0].chunk_id == "relevant"
    for result in out:
        assert result.source_metadata["rerank_strategy"] == "tfidf"
        assert result.source_metadata["rerank_degraded"] == "cross_encoder_error"
    assert _count("cross_encoder_error") == before + 1


async def test_default_path_labels_a_hosted_fallback_honestly() -> None:
    from app.rag_platform.hosted_reranker import HostedRerankerError

    class _Down:
        last_model = "rerank-x"

        async def rerank(self, query: str, documents: list[str], top_k: int | None = None) -> Any:
            raise HostedRerankerError("every rerank target failed")

    settings = SimpleNamespace(
        rag_default_rerank_enabled=True, rag_default_rerank_strategy="hosted"
    )
    before = _count("hosted_reranker_error")
    with patch(
        "app.rag_platform.registry_reranker.reranker_chain_from_settings", return_value=_Down()
    ):
        out = await apply_default_rerank(
            _results(), query="python", query_embedding=None, settings=settings
        )

    assert {r.source_metadata["rerank_strategy"] for r in out} == {"tfidf"}
    assert {r.source_metadata["rerank_degraded"] for r in out} == {"hosted_reranker_error"}
    assert _count("hosted_reranker_error") == before + 1


async def test_a_working_hosted_rerank_keeps_its_label() -> None:
    class _Up:
        last_model = "rerank-x"

        async def rerank(self, query: str, documents: list[str], top_k: int | None = None) -> Any:
            return [(1, 0.99), (0, 0.10)]

    settings = SimpleNamespace(
        rag_default_rerank_enabled=True, rag_default_rerank_strategy="hosted"
    )
    with patch(
        "app.rag_platform.registry_reranker.reranker_chain_from_settings", return_value=_Up()
    ):
        out = await apply_default_rerank(
            _results(), query="python", query_embedding=None, settings=settings
        )

    assert out[0].chunk_id == "relevant"
    assert {r.source_metadata["rerank_strategy"] for r in out} == {"hosted"}
    assert all("rerank_degraded" not in r.source_metadata for r in out)
