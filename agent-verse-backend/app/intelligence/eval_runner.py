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
        profile = context.get("_runtime_profile")
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

    async def _score_coherence(self, goal: str, steps: list[Any], provider: Any) -> float:
        """Use LLM to rate how logically coherent the steps are relative to the goal.

        Returns a float in [0.0, 1.0].  Conservative default 0.7 on any error.
        """
        if not steps or provider is None:
            return 0.5
        try:
            step_text = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps[:10]))
            prompt = (
                f"Goal: {goal}\n\nSteps taken:\n{step_text}\n\n"
                "Rate how logically coherent and relevant the steps are to achieving the goal. "
                "Score 0.0 (completely irrelevant) to 1.0 (perfectly coherent). "
                "Reply with ONLY a decimal number."
            )
            from app.providers.base import CompletionRequest, Message

            resp = await provider.complete(
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model="",
                    max_tokens=10,
                )
            )
            return min(1.0, max(0.0, float(resp.content.strip())))
        except Exception:
            return 0.7  # conservative default on failure

    async def _score_accuracy(
        self,
        goal: str,
        steps: list[Any],
        verification_feedback: str,
        verification_success: bool,
        provider: Any,
    ) -> float:
        """Use LLM to rate how accurately the agent achieved the goal.

        Falls back to the heuristic (verification_success) when provider is None
        or when the LLM call fails.  Returns float in [0.0, 1.0].
        """
        # Compute heuristic so we can return it on fallback
        feedback = (verification_feedback or "").lower()
        heuristic = 1.0 if verification_success else (0.5 if "partial" in feedback else 0.0)
        if provider is None:
            return heuristic
        try:
            step_text = (
                "\n".join(
                    f"{i + 1}. {getattr(s, 'description', str(s))}"
                    for i, s in enumerate(steps[:10])
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
            from app.providers.base import CompletionRequest, Message

            resp = await provider.complete(
                CompletionRequest(
                    messages=[Message(role="user", content=prompt)],
                    model="",
                    max_tokens=10,
                )
            )
            return min(1.0, max(0.0, float(resp.content.strip())))
        except Exception:
            return heuristic  # conservative fallback

    async def score_async(
        self,
        *,
        state: AgentState,
        tenant_ctx: TenantContext,
        provider: Any = None,
    ) -> EvalScorecard:
        """Score asynchronously, replacing heuristic coherence AND accuracy with LLM scoring."""
        scorecard = self.score(state=state, tenant_ctx=tenant_ctx)
        # Replace heuristic coherence with LLM-based coherence
        step_descriptions = [s.description for s in state.steps if s.description]
        coherence = await self._score_coherence(state.goal, step_descriptions, provider)
        scorecard.scores["coherence"] = coherence
        # Replace heuristic accuracy with LLM-based accuracy (Task 1)
        accuracy = await self._score_accuracy(
            goal=state.goal,
            steps=state.steps,
            verification_feedback=state.verification_feedback,
            verification_success=state.verification_success,
            provider=provider,
        )
        scorecard.scores["accuracy"] = accuracy
        return scorecard

    async def score_and_persist(
        self,
        state: AgentState,
        tenant_ctx: TenantContext,
        *,
        provider: Any = None,
        db: Any = None,
    ) -> EvalScorecard:
        """Score AND persist results to the evaluations table.

        Writes to the actual DB schema:
          - scores (JSON)  — full dimension breakdown
          - average_score (float)
          - passed (bool)
          - created_at (timestamp)
        """
        scorecard = await self.score_async(state=state, tenant_ctx=tenant_ctx, provider=provider)

        if db is not None:
            identity = ":".join(
                (
                    tenant_ctx.tenant_id,
                    state.goal_id,
                    scorecard.strategy_execution_id,
                    scorecard.evaluator_version,
                )
            )
            eval_id = uuid.uuid5(uuid.NAMESPACE_URL, identity).hex
            try:
                async with (
                    db() as session,
                    session.begin(),
                    sqlalchemy_rls_context(session, tenant_ctx.tenant_id),
                ):
                    await session.execute(
                        text("""
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
                        DO NOTHING
                        """),
                        {
                            "id": eval_id,
                            "gid": state.goal_id,
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
                            "evidence_completeness": json.dumps(
                                scorecard.evidence_completeness
                            ),
                            "correlation_id": scorecard.correlation_id,
                            "causation_id": scorecard.causation_id,
                        },
                    )
            except Exception as exc:
                get_logger(__name__).warning("eval_persist_failed", error=str(exc))

        return scorecard
