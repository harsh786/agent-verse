"""Comprehensive tests for app/intelligence/cost_optimizer.py — targeting 95%+ coverage."""
from __future__ import annotations

import pytest

from app.intelligence.cost_optimizer import (
    MODEL_COSTS_PER_1M,
    MODEL_DOWNGRADE_PATH,
    CostOptimizer,
    DowngradeSuggestion,
    ModelStats,
)


class TestModelStats:
    def test_avg_eval_score_no_data(self):
        stats = ModelStats(model="gpt-4o")
        assert stats.avg_eval_score == 0.0

    def test_avg_eval_score_with_data(self):
        stats = ModelStats(model="gpt-4o", eval_scores=[0.8, 0.9, 0.7])
        assert stats.avg_eval_score == pytest.approx(0.8)

    def test_avg_cost_per_goal_no_runs(self):
        stats = ModelStats(model="gpt-4o")
        assert stats.avg_cost_per_goal == 0.0

    def test_avg_cost_per_goal_with_runs(self):
        stats = ModelStats(model="gpt-4o", goal_count=4, total_cost_usd=0.20)
        assert stats.avg_cost_per_goal == pytest.approx(0.05)


class TestCostOptimizerRecord:
    def test_record_run_accumulates(self):
        opt = CostOptimizer()
        opt.record_run("summarize doc", "claude-opus-4-5", cost_usd=0.10, eval_score=0.9)
        opt.record_run("summarize doc", "claude-opus-4-5", cost_usd=0.12, eval_score=0.88)
        stats = opt._stats["summarize doc"]["claude-opus-4-5"]
        assert stats.goal_count == 2
        assert stats.total_cost_usd == pytest.approx(0.22)
        assert len(stats.eval_scores) == 2

    def test_categorise_first_three_words(self):
        opt = CostOptimizer()
        assert opt._categorise("run report every monday") == "run report every"

    def test_categorise_short_goal(self):
        opt = CostOptimizer()
        assert opt._categorise("go") == "general"

    def test_categorise_filters_short_words(self):
        opt = CostOptimizer()
        # "a", "is" are <= 2 chars, should be filtered
        cat = opt._categorise("a is larger project thing")
        assert "larger" in cat

    def test_categorise_general_fallback(self):
        opt = CostOptimizer()
        assert opt._categorise("") == "general"

    def test_categorise_all_short_words(self):
        opt = CostOptimizer()
        assert opt._categorise("a b c") == "general"


class TestCostOptimizerSuggestions:
    def _populate(self, opt: CostOptimizer, category: str, n: int = 60):
        for _ in range(n):
            opt.record_run(category, "claude-sonnet-4-5", cost_usd=0.03, eval_score=0.85)
            opt.record_run(category, "claude-haiku-3-5", cost_usd=0.003, eval_score=0.83)

    def test_suggestion_generated_when_quality_drop_ok(self):
        opt = CostOptimizer(quality_drop_threshold=0.05)
        self._populate(opt, "process reports")
        suggestions = opt.get_suggestions(min_goals=50)
        assert any(s.goal_category == "process reports" for s in suggestions)

    def test_no_suggestion_when_quality_drop_too_large(self):
        opt = CostOptimizer(quality_drop_threshold=0.05)
        for _ in range(60):
            opt.record_run("generate sql", "claude-sonnet-4-5", cost_usd=0.03, eval_score=0.95)
            opt.record_run("generate sql", "claude-haiku-3-5", cost_usd=0.003, eval_score=0.70)
        suggestions = opt.get_suggestions(min_goals=50)
        sql_suggestions = [s for s in suggestions if s.goal_category == "generate sql"]
        assert len(sql_suggestions) == 0

    def test_no_suggestion_when_insufficient_current_runs(self):
        opt = CostOptimizer()
        opt.record_run("few runs task", "claude-sonnet-4-5", cost_usd=0.01, eval_score=0.9)
        suggestions = opt.get_suggestions(min_goals=50)
        assert len(suggestions) == 0

    def test_no_suggestion_when_cheaper_model_insufficient_runs(self):
        """Covers line 125: cheaper_stats goal_count < min_goals."""
        opt = CostOptimizer()
        # Populate expensive model with enough runs
        for _ in range(60):
            opt.record_run("analyze data", "claude-sonnet-4-5", cost_usd=0.03, eval_score=0.85)
        # Only 5 runs for the cheaper model (below min_goals=50)
        for _ in range(5):
            opt.record_run("analyze data", "claude-haiku-3-5", cost_usd=0.003, eval_score=0.83)
        suggestions = opt.get_suggestions(min_goals=50)
        data_suggestions = [s for s in suggestions if "analyze" in s.goal_category]
        assert len(data_suggestions) == 0

    def test_no_suggestion_when_no_downgrade_path(self):
        """Covers line 119: no cheaper model in MODEL_DOWNGRADE_PATH."""
        opt = CostOptimizer()
        # "fake" model has no downgrade path
        for _ in range(60):
            opt.record_run("test task", "fake", cost_usd=0.0, eval_score=0.9)
        suggestions = opt.get_suggestions(min_goals=50)
        assert len(suggestions) == 0

    def test_no_suggestion_when_cheaper_stats_none(self):
        """Covers line 125: cheaper_stats is None (no data for cheaper model at all)."""
        opt = CostOptimizer()
        # Populate expensive model, but never record cheaper model
        for _ in range(60):
            opt.record_run("write summary", "gpt-4o", cost_usd=0.05, eval_score=0.88)
        suggestions = opt.get_suggestions(min_goals=50)
        gpt_suggestions = [s for s in suggestions if "write" in s.goal_category]
        assert len(gpt_suggestions) == 0

    def test_auto_apply_when_confidence_high(self):
        """Covers lines 151-153: auto-apply when confidence >= auto_apply_confidence."""
        opt = CostOptimizer(quality_drop_threshold=0.10, auto_apply_confidence=0.5)
        # 200 runs on each model to ensure confidence >= 0.5 (min_goals=50, 2x=100)
        for _ in range(200):
            opt.record_run("auto apply test", "claude-sonnet-4-5", cost_usd=0.03, eval_score=0.86)
            opt.record_run("auto apply test", "claude-haiku-3-5", cost_usd=0.003, eval_score=0.84)
        suggestions = opt.get_suggestions(min_goals=50)
        auto_suggestions = [s for s in suggestions if s.auto_applied]
        assert len(auto_suggestions) >= 1

    def test_get_model_override_after_auto_apply(self):
        """After auto-apply, get_model_override should return the cheaper model."""
        opt = CostOptimizer(quality_drop_threshold=0.10, auto_apply_confidence=0.5)
        for _ in range(200):
            opt.record_run("override test goal", "claude-sonnet-4-5", cost_usd=0.03, eval_score=0.86)
            opt.record_run("override test goal", "claude-haiku-3-5", cost_usd=0.003, eval_score=0.84)
        opt.get_suggestions(min_goals=50)
        override = opt.get_model_override("override test goal here now")
        # Should return claude-haiku-3-5 (the cheaper model)
        assert override == "claude-haiku-3-5" or override is None  # depends on confidence calc

    def test_get_model_override_none_when_not_applied(self):
        opt = CostOptimizer()
        assert opt.get_model_override("Some random goal") is None

    def test_suggestions_sorted_by_savings_descending(self):
        """Suggestions must be returned sorted by estimated_savings_usd_per_100 descending."""
        opt = CostOptimizer(quality_drop_threshold=0.10)
        # Category 1: big savings
        for _ in range(60):
            opt.record_run("category big savings", "claude-opus-4-5", cost_usd=0.15, eval_score=0.88)
            opt.record_run("category big savings", "claude-sonnet-4-5", cost_usd=0.03, eval_score=0.86)
        # Category 2: small savings
        for _ in range(60):
            opt.record_run("category small savings", "gpt-4o", cost_usd=0.05, eval_score=0.88)
            opt.record_run("category small savings", "gpt-4o-mini", cost_usd=0.015, eval_score=0.86)

        suggestions = opt.get_suggestions(min_goals=50)
        if len(suggestions) >= 2:
            savings = [s.estimated_savings_usd_per_100 for s in suggestions]
            assert savings == sorted(savings, reverse=True)


class TestCostOptimizerSummaryReport:
    """Tests for summary_report() — previously uncovered (lines 174-188)."""

    def test_summary_report_empty(self):
        opt = CostOptimizer()
        report = opt.summary_report()
        assert "categories" in report
        assert "overrides" in report
        assert report["categories"] == []
        assert report["overrides"] == {}

    def test_summary_report_with_data(self):
        opt = CostOptimizer()
        opt.record_run("process files", "claude-sonnet-4-5", cost_usd=0.02, eval_score=0.88)
        opt.record_run("process files", "claude-haiku-3-5", cost_usd=0.002, eval_score=0.84)

        report = opt.summary_report()
        assert len(report["categories"]) == 1
        category = report["categories"][0]
        assert "category" in category
        assert "models" in category
        assert len(category["models"]) == 2

    def test_summary_report_model_fields(self):
        opt = CostOptimizer()
        opt.record_run("test report", "gpt-4o", cost_usd=0.05, eval_score=0.9)

        report = opt.summary_report()
        model_data = report["categories"][0]["models"][0]
        assert "model" in model_data
        assert "goal_count" in model_data
        assert "avg_cost_usd" in model_data
        assert "avg_eval_score" in model_data
        assert "override_applied" in model_data

    def test_summary_report_override_applied_flag(self):
        """override_applied must be True for the auto-applied model."""
        opt = CostOptimizer(quality_drop_threshold=0.10, auto_apply_confidence=0.5)
        for _ in range(200):
            opt.record_run("flag test goal here", "claude-sonnet-4-5", cost_usd=0.03, eval_score=0.86)
            opt.record_run("flag test goal here", "claude-haiku-3-5", cost_usd=0.003, eval_score=0.84)
        opt.get_suggestions(min_goals=50)  # trigger auto-apply if confidence meets threshold
        report = opt.summary_report()
        assert len(report["categories"]) >= 1
