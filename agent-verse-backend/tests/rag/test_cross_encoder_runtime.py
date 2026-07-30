"""Concurrency and compatibility coverage for cross-encoder reranking."""

from __future__ import annotations

import asyncio
import math
import threading
import time
from typing import Any
from unittest.mock import patch

import pytest

from app.context.rerank_policy import RerankPolicy, RerankStrategy
from app.rag.cross_encoder import (
    CrossEncoderReranker,
    close_default_cross_encoder,
    cross_encode,
)
from app.rag_platform.reranker import RerankerInferenceError, RerankerLoadError


class RecordingCrossEncoder:
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def predict(
        self,
        pairs: list[tuple[str, str]],
        *,
        batch_size: int,
    ) -> list[float]:
        del batch_size
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.03)
        with self._lock:
            self.active -= 1
        return [float(index) for index, _ in enumerate(pairs)]


async def test_cross_encoder_loads_once_and_serializes_inference() -> None:
    load_count = 0
    backend = RecordingCrossEncoder()

    def load() -> RecordingCrossEncoder:
        nonlocal load_count
        load_count += 1
        time.sleep(0.02)
        return backend

    reranker = CrossEncoderReranker(
        model_loader=load,
        max_workers=1,
        max_queue_size=1,
    )

    results = await asyncio.gather(
        reranker.score("query", ["first"]),
        reranker.score("query", ["second"]),
    )

    assert results == [[0.0], [0.0]]
    assert load_count == 1
    assert backend.max_active == 1
    await reranker.aclose()


@pytest.mark.parametrize(
    "scores",
    [
        [float("nan")],
        [float("inf")],
        ["not-a-number"],
        ["0.5"],
        [True],
        [],
        [0.1, 0.2],
    ],
)
async def test_cross_encoder_rejects_invalid_scores(scores: list[Any]) -> None:
    class InvalidBackend:
        def predict(
            self,
            pairs: list[tuple[str, str]],
            *,
            batch_size: int,
        ) -> list[Any]:
            del pairs, batch_size
            return scores

    reranker = CrossEncoderReranker(model_loader=InvalidBackend)

    with pytest.raises(RerankerInferenceError):
        await reranker.score("query", ["document"])
    await reranker.aclose()


def test_direct_cross_encode_preserves_tfidf_fallback() -> None:
    reranker = CrossEncoderReranker(
        model_loader=lambda: (_ for _ in ()).throw(RerankerLoadError("unavailable"))
    )
    try:
        with patch(
            "app.rag.cross_encoder._get_default_reranker",
            return_value=reranker,
        ):
            scores = cross_encode("python", ["python tutorial", "weather report"])
    finally:
        reranker.close_sync()

    assert len(scores) == 2
    assert all(math.isfinite(score) for score in scores)
    assert scores[0] > scores[1]


def test_non_default_legacy_reranker_is_closed_after_use() -> None:
    with (
        patch.object(CrossEncoderReranker, "score_sync", return_value=[0.5]),
        patch.object(CrossEncoderReranker, "close_sync") as close,
    ):
        assert cross_encode("query", ["document"], batch_size=8) == [0.5]

    close.assert_called_once_with()


async def test_default_cross_encoder_has_explicit_async_shutdown_owner() -> None:
    reranker = CrossEncoderReranker(model_loader=RecordingCrossEncoder)
    with (
        patch("app.rag.cross_encoder._default_reranker", reranker),
        patch.object(reranker, "aclose") as close,
    ):
        await close_default_cross_encoder()

    close.assert_awaited_once_with()


def test_rerank_policy_preserves_legacy_fallback_when_wrapper_fails() -> None:
    chunks = [
        {"chunk_id": "relevant", "content": "python tutorial", "score": 0.2},
        {"chunk_id": "other", "content": "weather report", "score": 0.9},
    ]
    policy = RerankPolicy(strategy=RerankStrategy.CROSS_ENCODER)

    with patch("app.rag.cross_encoder.cross_encode", side_effect=RuntimeError("failed")):
        reranked = policy.rerank(chunks, query="python")

    assert reranked[0]["chunk_id"] == "relevant"
