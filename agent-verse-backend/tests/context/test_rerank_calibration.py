"""Cross-encoder availability + calibrated confidence in RerankPolicy (row 6)."""

from __future__ import annotations

from typing import Any

import pytest

from app.context.rerank_policy import RerankPolicy, RerankStrategy


def _chunks() -> list[dict[str, Any]]:
    return [
        {"chunk_id": "a", "content": "alpha", "score": 0.9},
        {"chunk_id": "b", "content": "beta", "score": 0.1},
        {"chunk_id": "c", "content": "gamma", "score": 0.5},
    ]


def test_cross_encoder_reorders_by_ce_score(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the cross-encoder is available it reorders by CE relevance."""

    def fake_cross_encode(query: str, docs: list[str], batch_size: int = 32) -> list[float]:
        mapping = {"alpha": 0.0, "beta": 10.0, "gamma": 2.0}
        return [mapping[d] for d in docs]

    monkeypatch.setattr("app.rag.cross_encoder.cross_encode", fake_cross_encode)
    policy = RerankPolicy(strategy=RerankStrategy.CROSS_ENCODER, max_per_source=0)
    result = policy.rerank(_chunks(), query="q")

    # "beta" had the lowest retrieval score but the highest CE relevance.
    assert result[0]["chunk_id"] == "b"
    assert "ce_score" in result[0]
    assert policy.last_strategy_used == RerankStrategy.CROSS_ENCODER


def test_cross_encoder_error_falls_back_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cross-encoder failure degrades to a lexical fallback and records why."""

    def boom(query: str, docs: list[str], batch_size: int = 32) -> list[float]:
        raise RuntimeError("model exploded")

    monkeypatch.setattr("app.rag.cross_encoder.cross_encode", boom)
    chunks = [
        {"chunk_id": "relevant", "content": "python tutorial", "score": 0.2},
        {"chunk_id": "other", "content": "weather report", "score": 0.9},
    ]
    policy = RerankPolicy(strategy=RerankStrategy.CROSS_ENCODER, max_per_source=0)
    result = policy.rerank(chunks, query="python")

    assert result[0]["chunk_id"] == "relevant"  # TF-IDF fallback, not a crash
    assert "fallback" in policy.last_reason


def test_auto_uses_cross_encoder_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rag.cross_encoder.is_cross_encoder_available", lambda: True)

    def fake_cross_encode(query: str, docs: list[str], batch_size: int = 32) -> list[float]:
        return [float(index) for index, _ in enumerate(docs)]

    monkeypatch.setattr("app.rag.cross_encoder.cross_encode", fake_cross_encode)
    policy = RerankPolicy(strategy=RerankStrategy.AUTO, max_per_source=0)
    policy.rerank(_chunks(), query="q")

    assert policy.last_strategy_used == RerankStrategy.CROSS_ENCODER
    assert "cross_encoder" in policy.last_reason


def test_auto_falls_back_to_score_when_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.rag.cross_encoder.is_cross_encoder_available", lambda: False)
    policy = RerankPolicy(strategy=RerankStrategy.AUTO, max_per_source=0)
    result = policy.rerank(_chunks(), query="q")

    # SCORE ordering: highest retrieval score first.
    assert result[0]["chunk_id"] == "a"
    assert policy.last_strategy_used == RerankStrategy.SCORE
    assert policy.last_reason  # a reason must always be recorded


def test_score_default_is_unchanged() -> None:
    policy = RerankPolicy()  # default strategy is SCORE, the safe path
    assert policy._strategy == RerankStrategy.SCORE
    result = policy.rerank(_chunks(), query="q")
    assert [c["chunk_id"] for c in result] == ["a", "c", "b"]


def test_rerank_attaches_calibrated_confidence() -> None:
    policy = RerankPolicy(strategy=RerankStrategy.SCORE, max_per_source=0)
    result = policy.rerank(_chunks(), query="q")

    for chunk in result:
        assert "calibrated_confidence" in chunk
        assert 0.0 <= chunk["calibrated_confidence"] <= 1.0
        assert "score" in chunk  # original field preserved (additive)
    # Confidence order tracks the reranked order.
    confidences = [c["calibrated_confidence"] for c in result]
    assert confidences == sorted(confidences, reverse=True)
    assert policy.last_retrieval_confidence is not None
    assert 0.0 <= policy.last_retrieval_confidence <= 1.0


def test_calibration_can_be_disabled() -> None:
    policy = RerankPolicy(strategy=RerankStrategy.SCORE, calibration_method=None)
    result = policy.rerank(_chunks(), query="q")
    assert all("calibrated_confidence" not in c for c in result)
