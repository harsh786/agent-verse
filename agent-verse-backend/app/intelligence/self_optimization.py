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
    # change_type drives the actual mutation in apply_suggestion():
    #   "improve_planner_prompt" | "improve_executor_prompt" | "add_domain_context"
    #   | "increase_iterations" | "add_tool_access"
    change_type: str = ""
    description: str = ""
    before: str = ""
    after: str = ""
    confidence: float = 0.0
    applied: bool = False
    rejected: bool = False
    tenant_id: str = ""


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
        """Apply a suggestion, mutating agent_config where applicable."""
        suggestions = self._suggestions.get(tenant_ctx.tenant_id, [])
        suggestion = next(
            (s for s in suggestions if s.suggestion_id == suggestion_id), None
        )
        if suggestion is None or suggestion.rejected:
            return False

        suggestion.applied = True

        # Actually apply the change based on change_type
        change_type = suggestion.change_type

        # 1. Prompt improvement — append to agent's goal_template and register A/B variant
        if change_type in (
            "improve_planner_prompt",
            "improve_executor_prompt",
            "add_domain_context",
        ):
            if agent_config is not None and "goal_template" in agent_config:
                existing = agent_config.get("goal_template", "")
                if suggestion.after and suggestion.after not in existing:
                    agent_config["goal_template"] = existing + f"\n\n{suggestion.after}"

            # Register as a prompt variant for A/B testing
            try:
                from app.intelligence.prompt_optimizer import PromptVariant, _default_optimizer

                variant = PromptVariant(
                    variant_id=suggestion.suggestion_id,
                    name=f"opt_{suggestion.suggestion_id[:8]}",
                    prompt_text=suggestion.after or "",
                    prompt_key=(
                        "planner" if "planner" in change_type else "executor"
                    ),
                )
                _default_optimizer.register_variant(
                    prompt_key=variant.prompt_key,
                    name=variant.name,
                    prompt_text=variant.prompt_text,
                    is_control=False,
                    tenant_id=tenant_ctx.tenant_id,
                )
            except Exception:
                pass

        # 2. Increase max_iterations
        elif change_type == "increase_iterations":
            if agent_config is not None:
                try:
                    agent_config["max_iterations"] = int(suggestion.after or "5")
                except ValueError:
                    agent_config["max_iterations"] = 5

        # 3. Add tool access — append connector_id
        elif change_type == "add_tool_access":
            if agent_config is not None:
                tool_name = suggestion.after or ""
                existing_connectors = list(agent_config.get("connector_ids", []))
                if tool_name and tool_name not in existing_connectors:
                    agent_config["connector_ids"] = existing_connectors + [tool_name]

        # Track the applied change
        import datetime as _dt

        self._applied_changes.setdefault(tenant_ctx.tenant_id, []).append({
            "suggestion_id": suggestion_id,
            "change_type": change_type,
            "before": suggestion.before,
            "after": suggestion.after,
            "applied_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "agent_config_mutated": agent_config is not None,
        })

        return True

    def get_applied_changes(self, *, tenant_ctx: TenantContext) -> list[dict]:
        """Return list of applied configuration changes for this tenant."""
        return self._applied_changes.get(tenant_ctx.tenant_id, [])

    def reject_suggestion(self, *, suggestion_id: str, tenant_ctx: TenantContext) -> bool:
        for s in self._suggestions.get(tenant_ctx.tenant_id, []):
            if s.suggestion_id == suggestion_id:
                s.rejected = True
                return True
        return False
