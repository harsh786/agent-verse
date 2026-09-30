"""Self-optimization — analyzes failed evaluations and produces improvement suggestions.

Suggestions can be applied automatically (prompt tuning) or presented to
human operators for review.
"""

from __future__ import annotations

import uuid
import warnings
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.db.rls import sqlalchemy_rls_context
from app.intelligence.eval import EvalScorecard
from app.tenancy.context import TenantContext

#: Per-tenant cap on the in-process suggestion list (the DB is authoritative
#: when wired; this only bounds a DB-less process and the RPA dedup window).
_MAX_SUGGESTIONS_PER_TENANT = 200

warnings.warn(
    "app.intelligence.self_optimization is deprecated. Use app.intelligence.self_optimizer_v2 instead. "  # noqa: E501
    "This module will be removed in a future release.",
    DeprecationWarning,
    stacklevel=2,
)


@dataclass
class OptimizationSuggestion:
    suggestion_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    category: str = ""  # "prompt" | "tool_selection" | "retry_strategy" | "context_size"
    # change_type drives the actual mutation in apply_suggestion():
    #   "improve_planner_prompt" | "improve_executor_prompt" | "add_domain_context"
    #   | "increase_iterations" | "decrease_iterations" | "add_tool_access"
    change_type: str = ""
    description: str = ""
    before: str = ""
    after: str = ""
    confidence: float = 0.0
    applied: bool = False
    rejected: bool = False
    tenant_id: str = ""
    created_at: str = ""


class SelfOptimizer:
    """Analyzes failed/low-scoring eval runs and generates improvement suggestions."""

    def __init__(self) -> None:
        self._suggestions: dict[str, list[OptimizationSuggestion]] = {}
        self._eval_history: dict[str, list[EvalScorecard]] = {}
        self._applied_changes: dict[str, list] = {}
        # Set by the app lifespan / worker: suggestions then live in Postgres
        # (tenant-scoped under RLS) and are shared across replicas/restarts.
        self._db: Any = None
        self._pending_writes: set[Any] = set()

    def _remember(self, tenant_id: str, suggestions: list[OptimizationSuggestion]) -> None:
        bucket = self._suggestions.setdefault(tenant_id, [])
        bucket.extend(suggestions)
        if len(bucket) > _MAX_SUGGESTIONS_PER_TENANT:
            del bucket[: len(bucket) - _MAX_SUGGESTIONS_PER_TENANT]

    def _schedule_persist(
        self, suggestions: list[OptimizationSuggestion], tenant_ctx: TenantContext
    ) -> None:
        """Persist from a sync caller: a tracked task (never a dropped one)."""
        if self._db is None or not suggestions:
            return
        import asyncio as _asyncio

        try:
            loop = _asyncio.get_running_loop()
        except RuntimeError:
            from app.observability.logging import get_logger

            get_logger(__name__).warning(
                "suggestion_persist_skipped_no_loop", tenant_id=tenant_ctx.tenant_id
            )
            return
        for sug in suggestions:
            task = loop.create_task(
                self.persist_suggestion(sug, tenant_ctx=tenant_ctx, db=self._db)
            )
            self._pending_writes.add(task)
            task.add_done_callback(self._pending_writes.discard)

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
                # change_type was never set here, so apply_suggestion() matched no
                # branch and "applied" nothing while reporting success.
                OptimizationSuggestion(
                    category="prompt",
                    change_type="improve_planner_prompt",
                    description=(
                        f"Goal scored {avg:.2f} — review planner instructions for "
                        "more precise task decomposition and completion criteria"
                    ),
                    confidence=0.7 if avg < 0.5 else 0.5,
                )
            )

        if "tool" in error_log.lower() or "not found" in error_log.lower():
            suggestions.append(
                OptimizationSuggestion(
                    category="tool_selection",
                    change_type="improve_executor_prompt",
                    description=(
                        "Tool lookup failure detected — expand available tool "
                        "context in executor prompt"
                    ),
                    confidence=0.8,
                )
            )

        efficiency = scorecard.get_score("efficiency")
        if efficiency is not None and efficiency < 0.4:
            suggestions.append(
                OptimizationSuggestion(
                    category="retry_strategy",
                    change_type="decrease_iterations",
                    description=(
                        "Efficiency is low — reduce max_iterations or add "
                        "early-termination on repeated steps"
                    ),
                    after="max_iterations=8 with repeated-step detection",
                    confidence=0.6,
                )
            )

        for sug in suggestions:
            sug.tenant_id = tenant_ctx.tenant_id
        self._remember(tenant_ctx.tenant_id, suggestions)
        return suggestions

    async def analyze_and_persist(
        self,
        *,
        goal: str,
        scorecard: EvalScorecard,
        error_log: str,
        tenant_ctx: TenantContext,
        goal_id: str = "",
    ) -> list[OptimizationSuggestion]:
        """Analyze a low-scoring goal and persist its suggestions (awaited)."""
        suggestions = self.analyze_and_suggest(
            goal=goal, scorecard=scorecard, error_log=error_log, tenant_ctx=tenant_ctx
        )
        if self._db is not None:
            for sug in suggestions:
                await self.persist_suggestion(
                    sug, tenant_ctx=tenant_ctx, db=self._db, source_goal_id=goal_id
                )
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
            suggestions.append(
                OptimizationSuggestion(
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
                )
            )

        # Pattern 2: CAPTCHA detected
        if "captcha" in error_lower:
            suggestions.append(
                OptimizationSuggestion(
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
                )
            )

        # Pattern 3: Network timeout / page not loaded
        if "timeout" in error_lower and (
            "network" in error_lower or "idle" in error_lower or "load" in error_lower
        ):
            suggestions.append(
                OptimizationSuggestion(
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
                )
            )

        # Pattern 4: Authentication failure
        if (
            "auth" in error_lower
            or "credential" in error_lower
            or ("invalid" in error_lower and "password" in error_lower)
        ):
            suggestions.append(
                OptimizationSuggestion(
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
                )
            )

        for sug in suggestions:
            sug.tenant_id = tenant_ctx.tenant_id
        self._remember(tenant_ctx.tenant_id, suggestions)
        self._schedule_persist(suggestions, tenant_ctx)
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
        suggestion = next((s for s in suggestions if s.suggestion_id == suggestion_id), None)
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
                    prompt_key=("planner" if "planner" in change_type else "executor"),
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

        # 2. Adjust max_iterations — `after` is "8" or prose like
        #    "max_iterations=8 with repeated-step detection"; use its first integer.
        elif change_type in ("increase_iterations", "decrease_iterations"):
            if agent_config is not None:
                import re as _re

                m = _re.search(r"\d+", suggestion.after or "")
                agent_config["max_iterations"] = int(m.group()) if m else 5

        # 3. Add tool access — append connector_id
        elif change_type == "add_tool_access":
            if agent_config is not None:
                tool_name = suggestion.after or ""
                existing_connectors = list(agent_config.get("connector_ids", []))
                if tool_name and tool_name not in existing_connectors:
                    agent_config["connector_ids"] = [*existing_connectors, tool_name]

        # Track the applied change
        import datetime as _dt

        self._applied_changes.setdefault(tenant_ctx.tenant_id, []).append(
            {
                "suggestion_id": suggestion_id,
                "change_type": change_type,
                "before": suggestion.before,
                "after": suggestion.after,
                "applied_at": _dt.datetime.now(_dt.UTC).isoformat(),
                "agent_config_mutated": agent_config is not None,
            }
        )

        return True

    async def alist_suggestions(
        self, *, tenant_ctx: TenantContext, applied: bool | None = None, limit: int = 200
    ) -> list[OptimizationSuggestion]:
        """Suggestions for the tenant — from Postgres when wired (shared across
        replicas and restarts), else this process's list. A DB failure raises."""
        if self._db is None:
            return self.list_suggestions(tenant_ctx=tenant_ctx, applied=applied)
        where_applied = "AND applied = :applied" if applied is not None else ""
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            rows = (
                await session.execute(
                    text(f"""
                    SELECT suggestion_id, category, change_type, description,
                           before_text, after_text, confidence, applied, rejected,
                           created_at
                      FROM self_optimization_suggestions
                     WHERE tenant_id = :tid {where_applied}
                     ORDER BY created_at DESC
                     LIMIT :lim
                """),
                    {
                        "tid": tenant_ctx.tenant_id,
                        "lim": max(1, limit),
                        **({"applied": applied} if applied is not None else {}),
                    },
                )
            ).fetchall()
        return [
            OptimizationSuggestion(
                suggestion_id=str(r[0]),
                category=str(r[1] or ""),
                change_type=str(r[2] or ""),
                description=str(r[3] or ""),
                before=str(r[4] or ""),
                after=str(r[5] or ""),
                confidence=float(r[6] or 0.0),
                applied=bool(r[7]),
                rejected=bool(r[8]),
                tenant_id=tenant_ctx.tenant_id,
                created_at=r[9].isoformat() if hasattr(r[9], "isoformat") else "",
            )
            for r in rows
        ]

    async def areject_suggestion(self, *, suggestion_id: str, tenant_ctx: TenantContext) -> bool:
        """Reject a suggestion durably (Postgres when wired). A DB failure raises."""
        local = self.reject_suggestion(suggestion_id=suggestion_id, tenant_ctx=tenant_ctx)
        if self._db is None:
            return local
        async with (
            self._db() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
        ):
            res = await session.execute(
                text(
                    "UPDATE self_optimization_suggestions SET rejected = TRUE "
                    "WHERE tenant_id = :tid AND suggestion_id = :sid"
                ),
                {"tid": tenant_ctx.tenant_id, "sid": suggestion_id},
            )
        return bool(getattr(res, "rowcount", 0))

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
        suggestion: OptimizationSuggestion,
        *,
        tenant_ctx: TenantContext,
        db: Any = None,
        source_goal_id: str = "",
    ) -> None:
        """Persist a suggestion to self_optimization_suggestions table.

        Runs under the tenant's RLS GUC (the table is FORCE ROW LEVEL SECURITY;
        without it a NOBYPASSRLS role rejected every insert). Non-fatal — a DB
        write failure is logged at warning and counted so suggestion generation
        never blocks goal execution, but it is never silent.
        """
        if db is None:
            return
        try:
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
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
                        # id == suggestion_id: re-persisting one suggestion is a no-op.
                        "id": suggestion.suggestion_id,
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

            get_logger(__name__).warning(
                "suggestion_persist_failed",
                tenant_id=tenant_ctx.tenant_id,
                suggestion_id=suggestion.suggestion_id,
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
