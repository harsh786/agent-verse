"""ModelScorer — scores model efficiency: cost and latency."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.agent.state import AgentState
    from app.orchestration.runtime_profile import GoalRuntimeProfile


class ModelScorer:
    def score(self, *, cost_usd: float, latency_ms: float, budget_usd: float = 10.0) -> float:
        if budget_usd <= 0:
            return 0.5
        cost_ratio = cost_usd / budget_usd
        cost_score = max(0.0, 1.0 - cost_ratio)
        latency_s = latency_ms / 1000.0
        if latency_s <= 5:
            latency_score = 1.0
        elif latency_s <= 30:
            latency_score = 1.0 - (latency_s - 5) / 25
        else:
            latency_score = 0.1
        return 0.5 * cost_score + 0.5 * latency_score

    def score_cost(self, profile: GoalRuntimeProfile, state: AgentState) -> float | None:
        """Score cost efficiency: how much below budget the goal executed."""
        max_cost = getattr(profile.model_plan, "max_cost_usd", 0.10) or 0.10
        # N4 fix: cost is stored in state.context["total_cost_usd"], not a direct attribute
        _cost_ctx = getattr(state, "context", {}) or {}
        actual_cost = float(
            _cost_ctx.get(
                "total_cost_usd",
                getattr(state, "total_cost_usd", 0.0),  # fallback to attr
            )
            or 0.0
        )
        if "total_cost_usd" not in _cost_ctx and not hasattr(state, "total_cost_usd"):
            return 0.8
        ratio = actual_cost / max_cost
        if ratio <= 0.3:
            return 1.0
        if ratio <= 0.6:
            return 0.8
        if ratio <= 1.0:
            return 0.6
        return max(0.0, 1.0 - (ratio - 1.0) * 0.5)

    def score_latency(self, state: AgentState) -> float | None:
        """Score latency: faster execution = higher score."""
        latency_ms = float(state.context.get("_latency_ms", 0) or 0)
        if "_latency_ms" not in state.context:
            return 0.75
        if latency_ms < 5_000:
            return 1.0
        if latency_ms < 15_000:
            return 0.8
        if latency_ms < 30_000:
            return 0.6
        if latency_ms < 60_000:
            return 0.4
        return 0.2
