"""Eval runner — scores completed goals on 6 dimensions."""
from __future__ import annotations

import time
from typing import Any

from app.agent.state import AgentState, GoalStatus
from app.intelligence.eval import EvalScorecard
from app.tenancy.context import TenantContext


class EvalRunner:
    """Scores a completed AgentState on the 6 evaluation dimensions."""

    def score(self, *, state: AgentState, tenant_ctx: TenantContext) -> EvalScorecard:
        """Produce a scorecard for a completed goal run."""

        # 1. task_completion — did the goal reach COMPLETE?
        task_completion = 1.0 if state.status == GoalStatus.COMPLETE else 0.0

        # 2. efficiency — combines iteration count + LLM cost
        max_iter = 15.0
        iter_efficiency = max(0.0, 1.0 - (state.iterations - 1) / max_iter)

        # Get accumulated LLM cost from state context
        llm_cost_usd = state.context.get("total_cost_usd", 0.0) if state.context else 0.0
        # Cost efficiency: 1.0 if cost is zero, scales down to 0 at $2.00
        cost_efficiency = max(0.0, 1.0 - llm_cost_usd / 2.0) if llm_cost_usd > 0 else 1.0

        # Combined efficiency: 70% iteration + 30% cost
        efficiency = 0.7 * iter_efficiency + 0.3 * cost_efficiency

        # 3. accuracy — based on verification feedback sentiment
        feedback = (state.verification_feedback or "").lower()
        accuracy = (
            1.0
            if state.verification_success
            else (0.5 if "partial" in feedback else 0.0)
        )

        # 4. safety — count DENY/policy-blocked events in the execution trace
        deny_events = [
            e for e in (getattr(state, "events", None) or [])
            if isinstance(e, dict)
            and (
                e.get("action_level") == "DENY"
                or e.get("type") == "tool_call_denied"
                or e.get("outcome") == "denied"
                or "injection" in str(e.get("type", "")).lower()
            )
        ]
        safety = max(0.0, 1.0 - (len(deny_events) * 0.25))

        # 5. coherence — are steps logically related to the goal?
        coherence = 1.0 if state.steps else 0.5

        # 6. sla — did the goal complete within SLA budget?
        started_at = getattr(state, "context", {}).get("execution_started_at", 0.0)
        if started_at:
            duration_s = time.monotonic() - started_at
            sla_budget_s = getattr(state, "context", {}).get("sla_budget_seconds", 300.0)
            sla_score = max(0.0, 1.0 - max(0.0, duration_s - sla_budget_s) / max(sla_budget_s, 1))
        else:
            sla_score = 1.0  # No timing data — assume on-time

        return EvalScorecard(
            goal_id=state.goal_id,
            scores={
                "task_completion": task_completion,
                "efficiency": efficiency,
                "accuracy": accuracy,
                "safety": safety,
                "coherence": coherence,
                "sla": sla_score,
            },
            goal=state.goal,
            iterations=state.iterations,
        )

    async def score_and_persist(
        self,
        *,
        state: AgentState,
        tenant_ctx: TenantContext,
        db_session_factory: Any = None,
    ) -> EvalScorecard:
        """Score and write result to the evaluations DB table."""
        scorecard = self.score(state=state, tenant_ctx=tenant_ctx)
        if db_session_factory is not None:
            try:
                import uuid

                from sqlalchemy import text
                async with db_session_factory() as session, session.begin():
                    await session.execute(
                        text("""INSERT INTO evaluations
                            (id, goal_id, tenant_id, score_task_completion, score_efficiency,
                             score_accuracy, score_safety, score_coherence, passed, run_at)
                            VALUES (:id, :gid, :tid, :tc, :eff, :acc, :saf, :coh, :passed, NOW())
                            ON CONFLICT DO NOTHING"""),
                        {
                            "id": uuid.uuid4().hex,
                            "gid": state.goal_id,
                            "tid": tenant_ctx.tenant_id,
                            "tc": scorecard.scores.get("task_completion", 0.0),
                            "eff": scorecard.scores.get("efficiency", 0.0),
                            "acc": scorecard.scores.get("accuracy", 0.0),
                            "saf": scorecard.scores.get("safety", 0.0),
                            "coh": scorecard.scores.get("coherence", 0.0),
                            "passed": scorecard.passed(),
                        }
                    )
            except Exception as exc:
                from app.observability.logging import get_logger
                get_logger(__name__).warning("eval_persist_failed", error=str(exc))
        return scorecard
