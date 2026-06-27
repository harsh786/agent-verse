"""CostOptimizer — tracks LLM cost per goal type and suggests model downgrades.

Strategy:
  - Group goals by category (inferred from first 3 words of goal text).
  - For each category, track cost and eval score per model.
  - If eval score for a cheaper model is within threshold of expensive model: suggest downgrade.
  - Auto-apply when confidence > threshold.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# Model cost per 1M tokens (input + output blended estimate)
MODEL_COSTS_PER_1M: dict[str, float] = {
    "claude-opus-4-5": 15.0,
    "claude-sonnet-4-5": 3.0,
    "claude-haiku-3-5": 0.25,
    "gpt-4o": 5.0,
    "gpt-4o-mini": 0.15,
    "gemini-1.5-pro": 3.5,
    "gemini-1.5-flash": 0.075,
    "fake": 0.0,
}

# Ordered from most expensive to cheapest (downgrade direction)
MODEL_DOWNGRADE_PATH: dict[str, str] = {
    "claude-opus-4-5": "claude-sonnet-4-5",
    "claude-sonnet-4-5": "claude-haiku-3-5",
    "gpt-4o": "gpt-4o-mini",
    "gemini-1.5-pro": "gemini-1.5-flash",
}


@dataclass
class ModelStats:
    model: str
    goal_count: int = 0
    total_cost_usd: float = 0.0
    eval_scores: list[float] = field(default_factory=list)

    @property
    def avg_eval_score(self) -> float:
        return statistics.mean(self.eval_scores) if self.eval_scores else 0.0

    @property
    def avg_cost_per_goal(self) -> float:
        return self.total_cost_usd / self.goal_count if self.goal_count > 0 else 0.0


@dataclass
class DowngradeSuggestion:
    goal_category: str
    current_model: str
    suggested_model: str
    estimated_savings_usd_per_100: float
    quality_drop_pct: float
    confidence: float
    auto_applied: bool = False


class CostOptimizer:
    """Analyses LLM spend per goal category and suggests cheaper models.

    Usage::

        optimizer = CostOptimizer()
        optimizer.record_run("Summarise file", "claude-opus-4-5", cost=0.05, eval_score=0.88)
        suggestions = optimizer.get_suggestions(min_goals=50)
    """

    def __init__(
        self,
        quality_drop_threshold: float = 0.05,
        auto_apply_confidence: float = 0.90,
    ) -> None:
        self._quality_drop_threshold = quality_drop_threshold
        self._auto_apply_confidence = auto_apply_confidence
        # category -> model -> ModelStats
        self._stats: dict[str, dict[str, ModelStats]] = defaultdict(
            lambda: defaultdict(lambda: ModelStats(model=""))
        )
        self._applied_overrides: dict[str, str] = {}  # category -> model

    def _categorise(self, goal: str) -> str:
        """Extract goal category from first 3 significant words."""
        words = [w.lower() for w in goal.split() if len(w) > 2][:3]
        return " ".join(words) if words else "general"

    def record_run(
        self,
        goal: str,
        model: str,
        cost_usd: float,
        eval_score: float,
    ) -> None:
        """Record a goal run's cost and quality metrics."""
        category = self._categorise(goal)
        stats = self._stats[category][model]
        stats.model = model
        stats.goal_count += 1
        stats.total_cost_usd += cost_usd
        stats.eval_scores.append(eval_score)

    def get_suggestions(self, min_goals: int = 50) -> list[DowngradeSuggestion]:
        """Return downgrade suggestions for categories with sufficient data."""
        suggestions: list[DowngradeSuggestion] = []

        for category, model_stats in self._stats.items():
            for current_model, stats in model_stats.items():
                if stats.goal_count < min_goals:
                    continue
                cheaper_model = MODEL_DOWNGRADE_PATH.get(current_model)
                if not cheaper_model:
                    continue
                cheaper_stats = model_stats.get(cheaper_model)
                if not cheaper_stats or cheaper_stats.goal_count < min_goals:
                    continue

                quality_drop = stats.avg_eval_score - cheaper_stats.avg_eval_score
                quality_drop_pct = (
                    quality_drop / stats.avg_eval_score if stats.avg_eval_score > 0 else 0.0
                )

                if quality_drop_pct > self._quality_drop_threshold:
                    continue  # quality drop too large

                savings_per_goal = stats.avg_cost_per_goal - cheaper_stats.avg_cost_per_goal
                savings_per_100 = savings_per_goal * 100

                # Confidence: ratio of runs on cheaper model
                confidence = min(cheaper_stats.goal_count / (min_goals * 2), 1.0)

                suggestion = DowngradeSuggestion(
                    goal_category=category,
                    current_model=current_model,
                    suggested_model=cheaper_model,
                    estimated_savings_usd_per_100=round(savings_per_100, 4),
                    quality_drop_pct=round(quality_drop_pct * 100, 2),
                    confidence=round(confidence, 3),
                )

                if confidence >= self._auto_apply_confidence:
                    self._applied_overrides[category] = cheaper_model
                    suggestion.auto_applied = True
                    logger.info(
                        "Auto-applied model downgrade: %s -> %s for category '%s' "
                        "(confidence=%.2f, quality_drop=%.1f%%)",
                        current_model,
                        cheaper_model,
                        category,
                        confidence,
                        quality_drop_pct * 100,
                    )

                suggestions.append(suggestion)

        return sorted(suggestions, key=lambda s: s.estimated_savings_usd_per_100, reverse=True)

    def get_model_override(self, goal: str) -> str | None:
        """Return the cost-optimised model for a goal category, if any."""
        category = self._categorise(goal)
        return self._applied_overrides.get(category)

    def summary_report(self) -> dict[str, Any]:
        """Return a full cost/quality summary for all tracked categories."""
        report: list[dict[str, Any]] = []
        for category, model_stats in self._stats.items():
            category_data: dict[str, Any] = {"category": category, "models": []}
            for model, stats in model_stats.items():
                category_data["models"].append(
                    {
                        "model": model,
                        "goal_count": stats.goal_count,
                        "avg_cost_usd": round(stats.avg_cost_per_goal, 6),
                        "avg_eval_score": round(stats.avg_eval_score, 4),
                        "override_applied": self._applied_overrides.get(category) == model,
                    }
                )
            report.append(category_data)
        return {"categories": report, "overrides": dict(self._applied_overrides)}
