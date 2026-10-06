"""a10-F234-04: the no-DB preview histogram has a bucket below 0.80 (unit)."""

from __future__ import annotations

from app.api.training_export import _memory_preview


def test_memory_preview_counts_scores_below_080_separately() -> None:
    examples = [{"eval_score": s} for s in (0.1, 0.79, 0.8, 0.86, 0.93, 0.99)]
    dist = _memory_preview(examples)["score_distribution"]
    assert dist == {
        "0.00-0.80": 2,
        "0.80-0.85": 1,
        "0.85-0.90": 1,
        "0.90-0.95": 1,
        "0.95-1.00": 1,
    }
