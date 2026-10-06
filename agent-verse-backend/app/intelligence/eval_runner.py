"""Eval runner — scores completed goals on 7 dimensions."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, ClassVar

from sqlalchemy import text

from app.agent.state import AgentState, GoalStatus
from app.db.rls import sqlalchemy_rls_context
from app.evals.scoring_config import EvalScoringConfig
from app.intelligence.eval import EvalScorecard
from app.observability.logging import get_logger
from app.tenancy.context import TenantContext


class EvalRunner:
    """Scores a completed AgentState on the 7 evaluation dimensions.

    Every weight, threshold and budget is sourced from
    :class:`app.evals.scoring_config.EvalScoringConfig` (backed by ``Settings``),
    so nothing about how a run is scored is hardcoded in this class.
    """

    EVALUATOR_VERSION = "eval-runner-v2"

    # All 7 scoring dimensions produced by this runner
    DIMENSIONS: ClassVar[list[str]] = [
        "task_completion",
        "efficiency",
        "accuracy",
        "safety",
        "coherence",
        "sla",
        "tool_relevance",
    ]

    def __init__(self, config: EvalScoringConfig | None = None) -> None:
        # Resolve lazily per-instance so env overrides picked up at construction.
        self._config = config

    @property
    def config(self) -> EvalScoringConfig:
        if self._config is None:
            self._config = EvalScoringConfig.from_settings()
        return self._config

    @property
    def score_dimensions(self) -> list[str]:
        """Return all 7 dimension names scored by this runner."""
        return self.DIMENSIONS

    def _score_tool_relevance(self, steps: list[Any], iterations: int) -> float:
        """Score tool call efficiency: redundant/failed calls lower the score.

        Returns a float in [0.0, 1.0]. Neutral defaults, the per-step target and
        the success/efficiency blend are all config-driven.
        """
        cfg = self.config
        if not steps:
            return cfg.neutral_no_data_score

        all_calls: list[Any] = []
        for s in steps:
            all_calls.extend(getattr(s, "tool_calls", None) or [])

        if not all_calls:
            return cfg.neutral_no_tool_calls_score  # steps exist but no tool calls

        # Count failed calls
        failed = sum(
            1
            for tc in all_calls
            if isinstance(tc, dict) and (tc.get("error") or tc.get("status") == "failed")
        )
        total = len(all_calls)
        success_rate = max(0.0, 1.0 - failed / total)

        # Efficiency: penalize tool calls per step above the configured target.
        avg_per_step = total / max(len(steps), 1)
        over_target = max(0.0, avg_per_step - cfg.tool_calls_per_step_target)
        efficiency = max(0.0, 1.0 - over_target / cfg.tool_efficiency_tolerance)

        return (
            cfg.tool_relevance_success_weight * success_rate
            + cfg.tool_relevance_efficiency_weight * efficiency
        )

    def score(self, *, state: AgentState, tenant_ctx: TenantContext) -> EvalScorecard:
        """Produce a scorecard for a completed goal run."""

        if state.tenant_ctx.tenant_id != tenant_ctx.tenant_id:
            raise PermissionError("evaluation tenant does not match goal tenant")

        cfg = self.config

        # 1. task_completion — did the goal reach COMPLETE?
        task_completion = 1.0 if state.status == GoalStatus.COMPLETE else 0.0

        # 2. efficiency — combines iteration count + LLM cost
        max_iter = cfg.max_iterations_budget
        iter_efficiency = max(0.0, 1.0 - (state.iterations - 1) / max_iter)

        # Get accumulated LLM cost from state context (guard against non-dict context)
        _ctx = getattr(state, "context", None)
        llm_cost_usd = float(_ctx.get("total_cost_usd", 0.0)) if isinstance(_ctx, dict) else 0.0
        # Cost efficiency: 1.0 if cost is zero, scales down to 0 at the configured budget
        cost_efficiency = (
            max(0.0, 1.0 - llm_cost_usd / cfg.cost_budget_usd) if llm_cost_usd > 0 else 1.0
        )

        # Combined efficiency: config-driven iteration vs cost blend
        efficiency = (
            cfg.efficiency_iter_weight * iter_efficiency
            + cfg.efficiency_cost_weight * cost_efficiency
        )

        # 3. accuracy — heuristic placeholder (replaced by LLM scoring in score_async)
        feedback = (state.verification_feedback or "").lower()
        accuracy = (
            1.0
            if state.verification_success
            else (cfg.accuracy_partial_credit if "partial" in feedback else 0.0)
        )

        # 4. safety — count DENY/policy-blocked events in the execution trace
        deny_events = [
            e
            for e in (getattr(state, "events", None) or [])
            if isinstance(e, dict)
            and (
                e.get("action_level") == "DENY"
                or e.get("type") == "tool_call_denied"
                or e.get("outcome") == "denied"
                or "injection" in str(e.get("type", "")).lower()
            )
        ]
        safety = max(0.0, 1.0 - (len(deny_events) * cfg.safety_violation_penalty))

        # 5. coherence — heuristic placeholder (replaced by LLM scoring in score_async)
        if not state.steps:
            coherence = cfg.neutral_no_data_score
        else:
            steps_with_output = sum(1 for s in state.steps if getattr(s, "output", ""))
            output_rate = steps_with_output / len(state.steps)
            unique_descriptions = len({getattr(s, "description", "") for s in state.steps})
            diversity = min(1.0, unique_descriptions / max(len(state.steps), 1))
            coherence = (
                cfg.coherence_output_weight * output_rate
                + cfg.coherence_diversity_weight * diversity
            )

        # 6. sla — did the goal complete within SLA budget?
        _ctx2 = getattr(state, "context", None)
        started_at = (
            float(_ctx2.get("execution_started_at", 0.0)) if isinstance(_ctx2, dict) else 0.0
        )
        sla_budget_s = (
            float(_ctx2.get("sla_budget_seconds", cfg.sla_budget_seconds))
            if isinstance(_ctx2, dict)
            else cfg.sla_budget_seconds
        )
        if started_at > 0:  # valid monotonic or epoch timestamp
            duration_s = time.monotonic() - started_at
            sla_score = max(0.0, 1.0 - max(0.0, duration_s - sla_budget_s) / max(sla_budget_s, 1))
        elif state.iterations and state.iterations > 1:
            # Proxy: use iteration count as a time proxy at the configured per-iter cost
            estimated_duration_s = state.iterations * cfg.sla_iteration_seconds
            over_budget = max(0.0, estimated_duration_s - sla_budget_s)
            sla_score = max(0.0, 1.0 - over_budget / max(sla_budget_s, 1))
        else:
            sla_score = 1.0  # No timing or iteration data — default

        # 7. tool_relevance — NEW: efficiency/quality of tool usage
        tool_relevance = self._score_tool_relevance(state.steps, state.iterations)

        context = state.context if isinstance(state.context, dict) else {}
        profile = context.get("_runtime_profile") or context.get("_observed_runtime_profile")
        profile_tenant = getattr(profile, "tenant_id", tenant_ctx.tenant_id)
        if profile_tenant != tenant_ctx.tenant_id:
            raise PermissionError("runtime profile tenant does not match goal tenant")
        primary = getattr(profile, "primary_strategy", None)
        auxiliaries = getattr(profile, "auxiliary_strategies", ()) or ()
        strategy_execution_id = str(
            context.get("strategy_execution_id")
            or context.get("execution_id")
            or f"legacy:{state.goal_id}"
        )
        evidence_completeness = {
            "task_completion": state.status in {GoalStatus.COMPLETE, GoalStatus.FAILED},
            "efficiency": state.iterations > 0 or "total_cost_usd" in context,
            "accuracy": bool(state.verification_feedback) or state.verification_success,
            "safety": isinstance(getattr(state, "events", None), list),
            "coherence": bool(state.steps),
            "sla": "execution_started_at" in context or state.iterations > 0,
            "tool_relevance": any(bool(getattr(step, "tool_calls", None)) for step in state.steps),
        }

        return EvalScorecard(
            goal_id=state.goal_id,
            scores={
                "task_completion": task_completion,
                "efficiency": efficiency,
                "accuracy": accuracy,
                "safety": safety,
                "coherence": coherence,
                "sla": sla_score,
                "tool_relevance": tool_relevance,
            },
            goal=state.goal,
            iterations=state.iterations,
            primary_strategy_id=str(getattr(primary, "strategy_id", "unknown")),
            primary_strategy_version=str(getattr(primary, "adapter_version", "unknown")),
            auxiliary_strategy_versions={
                str(item.strategy_id): str(item.adapter_version) for item in auxiliaries
            },
            profile_id=str(getattr(profile, "profile_id", "unknown")),
            profile_version=int(getattr(profile, "profile_version", 0)),
            strategy_execution_id=strategy_execution_id,
            evaluator_version=self.EVALUATOR_VERSION,
            evidence_completeness=evidence_completeness,
            correlation_id=str(context.get("correlation_id", "")),
            causation_id=str(context.get("causation_id", "")),
        )

    async def _llm_rate(
        self,
        prompt: str,
        provider: Any,
        *,
        role: str,
        tenant_ctx: Any,
        goal_id: str | None,
    ) -> float | None:
        """One charged, circuit-broken 0..1 rating from the model; None on any failure."""
        try:
            from app.ai_router.role_preference import preferred_model_and_fallbacks
            from app.providers.base import CompletionRequest, Message
            from app.providers.guarded_completion import complete_decision

            # The saved reasoning order picks the judge model (with failover);
            # without one the provider default answers, as before.
            model, fallbacks = preferred_model_and_fallbacks("judge", provider)
            resp = await complete_decision(
                provider,
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model=model,
                    max_tokens=10,
                ),
                role=role,
                tenant_ctx=tenant_ctx,
                goal_id=goal_id,
                fallback_models=fallbacks,
            )
            return min(1.0, max(0.0, float(resp.content.strip())))
        except Exception:
            return None

    async def _llm_coherence(
        self,
        goal: str,
        steps: list[Any],
        provider: Any,
        *,
        tenant_ctx: Any = None,
        goal_id: str | None = None,
    ) -> float | None:
        """The model's coherence rating, or None when it was not (or could not be) asked."""
        if not steps or provider is None:
            return None
        step_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps[:10]))
        prompt = (
            f"Goal: {goal}\n\nSteps taken:\n{step_text}\n\n"
            "Rate how logically coherent and relevant the steps are to achieving the goal. "
            "Score 0.0 (completely irrelevant) to 1.0 (perfectly coherent). "
            "Reply with ONLY a decimal number."
        )
        # Charged to the evaluated goal's tenant and circuit-broken.
        return await self._llm_rate(
            prompt, provider, role="eval_coherence", tenant_ctx=tenant_ctx, goal_id=goal_id
        )

    async def _score_coherence(
        self,
        goal: str,
        steps: list[Any],
        provider: Any,
        *,
        tenant_ctx: Any = None,
        goal_id: str | None = None,
    ) -> float:
        """Use LLM to rate how logically coherent the steps are relative to the goal.

        Returns a float in [0.0, 1.0]: 0.5 when there is nothing to ask about,
        a conservative 0.7 when the model call fails.
        """
        if not steps or provider is None:
            return 0.5
        rated = await self._llm_coherence(
            goal, steps, provider, tenant_ctx=tenant_ctx, goal_id=goal_id
        )
        return 0.7 if rated is None else rated

    @staticmethod
    def _accuracy_heuristic(verification_feedback: str, verification_success: bool) -> float:
        feedback = (verification_feedback or "").lower()
        return 1.0 if verification_success else (0.5 if "partial" in feedback else 0.0)

    async def _llm_accuracy(
        self,
        goal: str,
        steps: list[Any],
        verification_feedback: str,
        verification_success: bool,
        provider: Any,
        *,
        tenant_ctx: Any = None,
        goal_id: str | None = None,
    ) -> float | None:
        """The model's accuracy rating, or None when it was not (or could not be) asked."""
        if provider is None:
            return None
        step_text = (
            "\n".join(
                f"{i + 1}. {getattr(s, 'description', str(s))}" for i, s in enumerate(steps[:10])
            )
            or "(no steps)"
        )
        verification_note = (
            f"Verification: {'passed' if verification_success else 'failed'}. "
            f"Feedback: {verification_feedback or 'none'}"
        )
        prompt = (
            f"Goal: {goal}\n\nSteps taken:\n{step_text}\n\n{verification_note}\n\n"
            "Rate how accurately the agent achieved the stated goal. "
            "Score 0.0 (completely wrong/missed the goal) to 1.0 (fully accurate). "
            "Reply with ONLY a decimal number."
        )
        # Charged to the evaluated goal's tenant and circuit-broken.
        return await self._llm_rate(
            prompt, provider, role="eval_accuracy", tenant_ctx=tenant_ctx, goal_id=goal_id
        )

    async def _score_accuracy(
        self,
        goal: str,
        steps: list[Any],
        verification_feedback: str,
        verification_success: bool,
        provider: Any,
        *,
        tenant_ctx: Any = None,
        goal_id: str | None = None,
    ) -> float:
        """Use LLM to rate how accurately the agent achieved the goal.

        Falls back to the heuristic (verification_success) when provider is None
        or when the LLM call fails.  Returns float in [0.0, 1.0].
        """
        rated = await self._llm_accuracy(
            goal,
            steps,
            verification_feedback,
            verification_success,
            provider,
            tenant_ctx=tenant_ctx,
            goal_id=goal_id,
        )
        if rated is None:
            return self._accuracy_heuristic(verification_feedback, verification_success)
        return rated

    async def score_async(
        self,
        *,
        state: AgentState,
        tenant_ctx: TenantContext,
        provider: Any = None,
    ) -> EvalScorecard:
        """Score asynchronously, replacing heuristic coherence AND accuracy with LLM scoring.

        ``scorecard.scorer`` records what actually happened: "llm" when both
        judgement dimensions came from the model, "partial" when one fell back,
        "heuristic" when neither did (no provider, or every call failed).
        """
        scorecard = self.score(state=state, tenant_ctx=tenant_ctx)
        step_descriptions = [s.description for s in state.steps if s.description]
        _goal_id = str(getattr(state, "goal_id", "") or "") or None
        coherence = await self._llm_coherence(
            state.goal, step_descriptions, provider, tenant_ctx=tenant_ctx, goal_id=_goal_id
        )
        accuracy = await self._llm_accuracy(
            state.goal,
            state.steps,
            state.verification_feedback,
            state.verification_success,
            provider,
            tenant_ctx=tenant_ctx,
            goal_id=_goal_id,
        )
        if coherence is None:
            # Same defaults as _score_coherence: nothing to ask → 0.5, failed call → 0.7.
            coherence = 0.5 if not step_descriptions or provider is None else 0.7
            coherence_by_llm = False
        else:
            coherence_by_llm = True
        scorecard.scores["coherence"] = coherence
        if accuracy is None:
            scorecard.scores["accuracy"] = self._accuracy_heuristic(
                state.verification_feedback, state.verification_success
            )
        else:
            scorecard.scores["accuracy"] = accuracy
        by_llm = int(coherence_by_llm) + int(accuracy is not None)
        scorecard.scorer = ("heuristic", "partial", "llm")[by_llm]
        return scorecard

    async def persist_scorecard(
        self,
        scorecard: EvalScorecard,
        *,
        goal_id: str,
        tenant_ctx: TenantContext,
        db: Any,
        replace: bool = False,
        strict: bool = False,
    ) -> bool:
        """Write *scorecard* to ``evaluations`` under the tenant's RLS context.

        The row identity is deterministic, so a retried completion-time score is
        a no-op (``replace=False``). An explicit re-score (``replace=True``)
        overwrites it and bumps ``created_at`` so every replica reads the new
        scores. Returns True when written; a failure is logged and returns False,
        or raises when ``strict``.
        """
        identity = ":".join(
            (
                tenant_ctx.tenant_id,
                goal_id,
                scorecard.strategy_execution_id,
                scorecard.evaluator_version,
            )
        )
        eval_id = uuid.uuid5(uuid.NAMESPACE_URL, identity).hex
        on_conflict = (
            "DO UPDATE SET scores = EXCLUDED.scores, "
            "average_score = EXCLUDED.average_score, passed = EXCLUDED.passed, "
            "evidence_completeness = EXCLUDED.evidence_completeness, created_at = NOW()"
            if replace
            else "DO NOTHING"
        )
        try:
            async with (
                db() as session,
                session.begin(),
                sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
            ):
                await session.execute(
                    text(f"""
                    INSERT INTO evaluations
                        (id, goal_id, tenant_id, scores, average_score, passed,
                         primary_strategy_id, primary_strategy_version,
                         auxiliary_strategy_versions, profile_id, profile_version,
                         strategy_execution_id, evaluator_version,
                         evidence_completeness, correlation_id, causation_id, created_at)
                    VALUES
                        (:id, :gid, :tid, CAST(:scores AS json), :avg, :passed,
                         :primary_strategy_id, :primary_strategy_version,
                         CAST(:auxiliary_strategy_versions AS jsonb), :profile_id,
                         :profile_version, :strategy_execution_id, :evaluator_version,
                         CAST(:evidence_completeness AS jsonb), :correlation_id,
                         :causation_id, NOW())
                    ON CONFLICT
                        (tenant_id, goal_id, strategy_execution_id, evaluator_version)
                    {on_conflict}
                    """),  # on_conflict is one of the two literals above
                    {
                        "id": eval_id,
                        "gid": goal_id,
                        "tid": tenant_ctx.tenant_id,
                        "scores": json.dumps(scorecard.scores),
                        "avg": round(scorecard.average_score(), 6),
                        "passed": scorecard.passed(),
                        "primary_strategy_id": scorecard.primary_strategy_id,
                        "primary_strategy_version": scorecard.primary_strategy_version,
                        "auxiliary_strategy_versions": json.dumps(
                            scorecard.auxiliary_strategy_versions
                        ),
                        "profile_id": scorecard.profile_id,
                        "profile_version": scorecard.profile_version,
                        "strategy_execution_id": scorecard.strategy_execution_id,
                        "evaluator_version": scorecard.evaluator_version,
                        "evidence_completeness": json.dumps(scorecard.evidence_completeness),
                        "correlation_id": scorecard.correlation_id,
                        "causation_id": scorecard.causation_id,
                    },
                )
        except Exception as exc:
            get_logger(__name__).warning("eval_persist_failed", goal_id=goal_id, error=str(exc))
            if strict:
                raise
            return False
        return True

    async def score_and_persist(
        self,
        state: AgentState,
        tenant_ctx: TenantContext,
        *,
        provider: Any = None,
        db: Any = None,
    ) -> EvalScorecard:
        """Score AND persist results to the evaluations table (idempotent per goal).

        Writes to the actual DB schema:
          - scores (JSON)  — full dimension breakdown
          - average_score (float)
          - passed (bool)
          - created_at (timestamp)
        """
        scorecard = await self.score_async(state=state, tenant_ctx=tenant_ctx, provider=provider)
        if db is not None:
            await self.persist_scorecard(
                scorecard, goal_id=state.goal_id, tenant_ctx=tenant_ctx, db=db
            )
        return scorecard
