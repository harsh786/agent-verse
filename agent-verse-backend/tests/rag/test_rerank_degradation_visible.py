"""KB-24: reranker degradations are visible, and the 'llm' strategy is honest.

``_llm_rerank_sync`` just called the cross-encoder while reporting ``llm``; a
default-path rerank failure passed results through with a DEBUG log only.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.context.rerank_policy import RerankPolicy, RerankStrategy
from app.observability.metrics import RERANK_DEGRADED_TOTAL
from app.rag.engine import RetrievalResult
from app.rag.rerank_stage import apply_default_rerank


def test_llm_strategy_reports_the_reranker_that_actually_ran() -> None:
    policy = RerankPolicy(strategy=RerankStrategy.LLM)
    policy.rerank([{"content": "a", "score": 0.5}, {"content": "b", "score": 0.4}], query="a")
    assert policy.last_strategy_used == RerankStrategy.CROSS_ENCODER
    assert "llm" in (policy.last_reason or "").lower()


def _results() -> list[RetrievalResult]:
    return [
        RetrievalResult(chunk_id=f"c{i}", content=f"text {i}", score=1.0 - i / 10, source_metadata={})
        for i in range(3)
    ]


async def test_a_rerank_failure_is_counted_flagged_and_warned(
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = SimpleNamespace(rag_default_rerank_enabled=True, rag_default_rerank_strategy="score")
    before = RERANK_DEGRADED_TOTAL.labels(reason="rerank_error")._value.get()
    with (
        patch.object(RerankPolicy, "rerank", side_effect=RuntimeError("model crashed")),
        caplog.at_level(logging.WARNING),
    ):
        out = await apply_default_rerank(
            _results(), query="q", query_embedding=None, settings=settings
        )
    assert [r.chunk_id for r in out] == ["c0", "c1", "c2"]  # honest passthrough
    assert all(r.source_metadata.get("rerank_degraded") is True for r in out)
    after = RERANK_DEGRADED_TOTAL.labels(reason="rerank_error")._value.get()
    assert after == before + 1
