"""Self-optimization — analyzes failed evaluations and produces improvement suggestions.

Suggestions can be applied automatically (prompt tuning) or presented to
human operators for review.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

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
        self._db: Any = None  # Set externally to enable async DB persistence

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
        """Analyze a completed eval and produce optimization suggestions."""
        suggestions: list[OptimizationSuggestion] = []

        avg = scorecard.average_score()

        # Continuous improvement: any non-perfect goal is worth a suggestion
        if avg < 0.9:
            suggestions.append(
                OptimizationSuggestion(
                    category="prompt",
                    description=(
                        f"Goal scored {avg:.2f} — review planner instructions for "
                        "more precise task decomposition and completion criteria"
                    ),
                    before="Current planner system prompt",
                    after="Enhanced prompt with domain-specific decomposition guidance",
                    confidence=0.7 if avg < 0.5 else 0.5,
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

        # Async-persist suggestions when DB is wired
        if self._db is not None:
            import asyncio as _asyncio
            for _s in suggestions:
                _s.tenant_id = tenant_ctx.tenant_id
                try:
                    loop = _asyncio.get_running_loop()
                    loop.create_task(
                        self.persist_suggestion(_s, tenant_ctx=tenant_ctx, db=self._db)
                    )
                except RuntimeError:
                    pass  # Not in async context — caller can persist separately

        return suggestions

    def analyze_rpa_failure(
        self,
        *,
        tool_name: str,
        error: str,
        url: str,
        tenant_ctx: TenantContext,
    ) -> list[OptimizationSuggestion]:
        """Generate RPA-specific suggestions based on tool failure pattern.

        Covers four patterns: fragile CSS selectors, CAPTCHA blocks,
        network timeouts, and authentication failures.
        Deduplicates: same url+error pattern adds at most once per session.
        """
        error_lower = error.lower()
        url_lower = url.lower()
        suggestions: list[OptimizationSuggestion] = []

        # Dedup key: avoid generating identical suggestions for the same url+error
        dedup_key = f"rpa:{url_lower[:60]}:{error_lower[:60]}"
        existing = self._suggestions.get(tenant_ctx.tenant_id, [])
        if any(dedup_key in (s.before + s.description) for s in existing):
            return []

        # Pattern 1: Fragile CSS selector (timeout on #id or .class)
        if (
            "timeout" in error_lower
            and ("element" in error_lower or "selector" in error_lower)
            and tool_name in ("rpa_click", "rpa_type", "rpa_extract_text")
        ):
            suggestions.append(OptimizationSuggestion(
                category="rpa_selector",
                change_type="improve_executor_prompt",
                description=(
                    f"CSS selector timeout on {url} — prefer visible-text selectors "
                    "over CSS IDs/classes which break on page changes"
                ),
                before=dedup_key,
                after=(
                    f"For rpa_click on {url}: use text='Button Label' instead of "
                    "selector='#id'. Attribute selectors (input[name=field]) are more "
                    "stable than IDs for rpa_type."
                ),
                confidence=0.80,
            ))

        # Pattern 2: CAPTCHA detected
        if "captcha" in error_lower:
            suggestions.append(OptimizationSuggestion(
                category="rpa_captcha",
                change_type="add_domain_context",
                description=(
                    f"CAPTCHA detected on {url} — add rpa_detect_captcha check "
                    "before login and call rpa_request_human_help when triggered"
                ),
                before=dedup_key,
                after=(
                    "Add rpa_detect_captcha() before any login step on this URL. "
                    "If captcha_detected=true, call "
                    "rpa_request_human_help(reason='CAPTCHA on login')."
                ),
                confidence=0.90,
            ))

        # Pattern 3: Network timeout / page not loaded
        if (
            "timeout" in error_lower
            and ("network" in error_lower or "idle" in error_lower
                 or "load" in error_lower)
        ):
            suggestions.append(OptimizationSuggestion(
                category="rpa_timing",
                change_type="improve_executor_prompt",
                description=(
                    f"Page load timeout on {url} — add rpa_wait_for_network_idle "
                    "after navigation and form submissions"
                ),
                before=dedup_key,
                after=(
                    f"After rpa_open_url(url='{url[:60]}') and after rpa_submit_form(), "
                    "always call rpa_wait_for_network_idle(timeout_ms=15000)."
                ),
                confidence=0.75,
            ))

        # Pattern 4: Authentication failure
        if (
            "auth" in error_lower
            or "credential" in error_lower
            or ("invalid" in error_lower and "password" in error_lower)
        ):
            suggestions.append(OptimizationSuggestion(
                category="rpa_credentials",
                change_type="improve_executor_prompt",
                description=(
                    f"Authentication failed on {url} — use vault:// credential "
                    "references instead of hardcoded values"
                ),
                before=dedup_key,
                after=(
                    "For rpa_type password fields: use text='vault://<server>/<key>' "
                    "so credentials are resolved from the secure vault at runtime."
                ),
                confidence=0.85,
            ))

        # Store in memory
        self._suggestions.setdefault(tenant_ctx.tenant_id, []).extend(suggestions)

        # Async-persist when DB is wired
        if self._db is not None:
            import asyncio as _asyncio
            for _s in suggestions:
                _s.tenant_id = tenant_ctx.tenant_id
                try:
                    loop = _asyncio.get_running_loop()
                    loop.create_task(
                        self.persist_suggestion(_s, tenant_ctx=tenant_ctx, db=self._db)
                    )
                except RuntimeError:
                    pass

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

    async def persist_suggestion(
        self,
        suggestion: "OptimizationSuggestion",
        *,
        tenant_ctx: "TenantContext",
        db: Any = None,
        source_goal_id: str = "",
    ) -> None:
        """Persist a suggestion to self_optimization_suggestions table.

        Non-fatal — DB write failure is logged and swallowed so suggestion
        generation never blocks goal execution.
        """
        if db is None:
            return
        try:
            async with db() as session, session.begin():
                await session.execute(
                    text("""
                        INSERT INTO self_optimization_suggestions
                            (id, tenant_id, suggestion_id, category, change_type,
                             description, before_text, after_text, confidence,
                             applied, rejected, source_goal_id, created_at)
                        VALUES
                            (:id, :tenant_id, :suggestion_id, :category, :change_type,
                             :description, :before_text, :after_text, :confidence,
                             :applied, :rejected, :source_goal_id, NOW())
                        ON CONFLICT (id) DO NOTHING
                    """),
                    {
                        "id": uuid.uuid4().hex,
                        "tenant_id": tenant_ctx.tenant_id,
                        "suggestion_id": suggestion.suggestion_id,
                        "category": suggestion.category,
                        "change_type": suggestion.change_type or "",
                        "description": suggestion.description or "",
                        "before_text": suggestion.before or "",
                        "after_text": suggestion.after or "",
                        "confidence": float(suggestion.confidence),
                        "applied": suggestion.applied,
                        "rejected": suggestion.rejected,
                        "source_goal_id": source_goal_id or "",
                    },
                )
        except Exception as exc:
            from app.observability.logging import get_logger
            get_logger(__name__).warning("suggestion_persist_failed", error=str(exc))
