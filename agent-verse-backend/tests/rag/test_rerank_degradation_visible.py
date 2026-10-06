"""KB-24: reranker degradations are visible, and there is no fake 'llm' strategy.

``_llm_rerank_sync`` just called the cross-encoder while reporting ``llm``; a
default-path rerank failure passed results through with a DEBUG log only.
a04-F073-01: 'llm' (still no LLM reranker behind it) is now refused outright.
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


def test_llm_is_not_a_rerank_strategy() -> None:
    with pytest.raises(ValueError):
        RerankStrategy("llm")
    assert "llm" not in {s.value for s in RerankStrategy}


def test_settings_refuse_the_llm_rerank_strategy() -> None:
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError, match="not implemented"):
        Settings(rag_default_rerank_strategy="llm")
    with pytest.raises(ValidationError, match="not a rerank strategy"):
        Settings(rag_default_rerank_strategy="magic")
    assert Settings(rag_default_rerank_strategy="Hosted").rag_default_rerank_strategy == "hosted"


def test_llm_never_warms_the_cross_encoder() -> None:
    from app.rag.cross_encoder import CROSS_ENCODER_STRATEGIES

    assert "llm" not in CROSS_ENCODER_STRATEGIES


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
