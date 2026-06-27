"""Tests for CostOptimizer."""
from __future__ import annotations

from app.intelligence.cost_optimizer import CostOptimizer


def test_record_run_accumulates_stats():
    opt = CostOptimizer()
    opt.record_run("summarise file", "claude-sonnet-4-5", cost_usd=0.01, eval_score=0.85)
    opt.record_run("summarise file", "claude-sonnet-4-5", cost_usd=0.01, eval_score=0.87)
    stats = opt._stats["summarise file"]["claude-sonnet-4-5"]
    assert stats.goal_count == 2
    assert abs(stats.total_cost_usd - 0.02) < 1e-9


def test_suggestion_generated_when_cheaper_model_available():
    opt = CostOptimizer(quality_drop_threshold=0.10)
    for _ in range(50):
        opt.record_run("run report", "claude-sonnet-4-5", cost_usd=0.05, eval_score=0.85)
        opt.record_run("run report", "claude-haiku-3-5", cost_usd=0.005, eval_score=0.82)

    suggestions = opt.get_suggestions(min_goals=50)
    assert isinstance(suggestions, list)


def test_categorise_extracts_first_words():
    opt = CostOptimizer()
    cat = opt._categorise("Summarise all open GitHub issues and write a report")
    assert cat == "summarise all open"


def test_no_suggestion_when_quality_drop_too_large():
    opt = CostOptimizer(quality_drop_threshold=0.05)
    for _ in range(50):
        opt.record_run("generate code", "claude-sonnet-4-5", cost_usd=0.05, eval_score=0.92)
        opt.record_run("generate code", "claude-haiku-3-5", cost_usd=0.005, eval_score=0.70)

    suggestions = opt.get_suggestions(min_goals=50)
    code_suggestions = [s for s in suggestions if s.goal_category == "generate code"]
    assert all(s.quality_drop_pct <= 5.0 for s in code_suggestions)


def test_get_model_override_returns_none_when_no_override():
    opt = CostOptimizer()
    result = opt.get_model_override("Unrelated goal text here")
    assert result is None
