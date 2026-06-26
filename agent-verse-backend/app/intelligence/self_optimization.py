"""Self-optimization — analyzes failed evaluations and produces improvement suggestions.

Suggestions can be applied automatically (prompt tuning) or presented to
human operators for review.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from app.intelligence.eval import EvalScorecard
from app.tenancy.context import TenantContext


@dataclass
class OptimizationSuggestion:
    suggestion_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    category: str = ""  # "prompt" | "tool_selection" | "retry_strategy" | "context_size"
    description: str = ""
    before: str = ""
    after: str = ""
    confidence: float = 0.0
    applied: bool = False
    rejected: bool = False


class SelfOptimizer:
    """Analyzes failed/low-scoring eval runs and generates improvement suggestions."""

    def __init__(self) -> None:
        self._suggestions: dict[str, list[OptimizationSuggestion]] = {}
        self._eval_history: dict[str, list[EvalScorecard]] = {}
        self._applied_changes: dict[str, list] = {}

    def record_eval(
        self, *, goal_id: str, scorecard: EvalScorecard, tenant_ctx: TenantContext
    ) -> None:
        tid = tenant_ctx.tenant_id
        self._eval_history.setdefault(tid, []).append(scorecard)

    def analyze_and_suggest(
        self,
        *,
        goal: str,
        scorecard: EvalScorecard,
        error_log: str,
        tenant_ctx: TenantContext,
    ) -> list[OptimizationSuggestion]:
        """Analyze a low-scoring eval and produce suggestions."""
        suggestions: list[OptimizationSuggestion] = []

        if scorecard.average_score() < 0.5:
            suggestions.append(
                OptimizationSuggestion(
                    category="prompt",
                    description=(
                        "Goal decomposition score is low — add more specific "
                        "planning instructions"
                    ),
                    before="Current planner system prompt",
                    after="Enhanced prompt with domain-specific decomposition guidance",
                    confidence=0.7,
                )
            )

        if "tool" in error_log.lower() or "not found" in error_log.lower():
            suggestions.append(
                OptimizationSuggestion(
                    category="tool_selection",
                    description=(
                        "Tool lookup failure detected — expand available tool "
                        "context in executor prompt"
                    ),
                    before="Tool list not injected into executor",
                    after="Inject discovered tools from MCP registry into executor system prompt",
                    confidence=0.8,
                )
            )

        efficiency = scorecard.get_score("efficiency")
        if efficiency is not None and efficiency < 0.4:
            suggestions.append(
                OptimizationSuggestion(
                    category="retry_strategy",
                    description=(
                        "Efficiency is low — reduce max_iterations or add "
                        "early-termination on repeated steps"
                    ),
                    before="max_iterations=15",
                    after="max_iterations=8 with repeated-step detection",
                    confidence=0.6,
                )
            )

        tid = tenant_ctx.tenant_id
        self._suggestions.setdefault(tid, []).extend(suggestions)
        return suggestions

    def list_suggestions(
        self, *, tenant_ctx: TenantContext, applied: bool | None = None
    ) -> list[OptimizationSuggestion]:
        subs = self._suggestions.get(tenant_ctx.tenant_id, [])
        if applied is not None:
            subs = [s for s in subs if s.applied == applied]
        return subs

    def apply_suggestion(
        self,
        *,
        suggestion_id: str,
        tenant_ctx: TenantContext,
        agent_config: dict | None = None,
    ) -> bool:
        """Apply a suggestion, optionally recording the agent config change."""
        for s in self._suggestions.get(tenant_ctx.tenant_id, []):
            if s.suggestion_id == suggestion_id:
                if s.rejected:
                    return False
                s.applied = True
                self._applied_changes.setdefault(tenant_ctx.tenant_id, []).append({
                    "suggestion_id": suggestion_id,
                    "category": s.category,
                    "description": s.description,
                    "applied_config": s.after if agent_config is None else str(agent_config),
                })
                return True
        return False

    def get_applied_changes(self, *, tenant_ctx: TenantContext) -> list[dict]:
        """Return list of applied configuration changes for this tenant."""
        return self._applied_changes.get(tenant_ctx.tenant_id, [])

    def reject_suggestion(self, *, suggestion_id: str, tenant_ctx: TenantContext) -> bool:
        for s in self._suggestions.get(tenant_ctx.tenant_id, []):
            if s.suggestion_id == suggestion_id:
                s.rejected = True
                return True
        return False
