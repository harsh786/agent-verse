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

        # Get accumulated LLM cost from state context (guard against non-dict context)
        _ctx = getattr(state, "context", None)
        llm_cost_usd = float(_ctx.get("total_cost_usd", 0.0)) if isinstance(_ctx, dict) else 0.0
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

        # 5. coherence — heuristic placeholder (replaced by LLM scoring in score_async)
        # Smarter heuristic: use step diversity and output completeness as proxies
        # for coherence until LLM scoring is available (sync path)
        if not state.steps:
            coherence = 0.5
        else:
            steps_with_output = sum(1 for s in state.steps if getattr(s, 'output', ''))
            output_rate = steps_with_output / len(state.steps)
            unique_descriptions = len({getattr(s, 'description', '') for s in state.steps})
            diversity = min(1.0, unique_descriptions / max(len(state.steps), 1))
            coherence = 0.6 * output_rate + 0.4 * diversity

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

    async def _score_coherence(self, goal: str, steps: list, provider: Any) -> float:
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
            resp = await provider.complete(CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model="",
                max_tokens=10,
            ))
            return min(1.0, max(0.0, float(resp.content.strip())))
        except Exception:
            return 0.7  # conservative default on failure

    async def score_async(
        self,
        *,
        state: AgentState,
        tenant_ctx: TenantContext,
        provider: Any = None,
    ) -> EvalScorecard:
        """Score asynchronously, replacing heuristic coherence with LLM-based scoring."""
        scorecard = self.score(state=state, tenant_ctx=tenant_ctx)
        # Replace heuristic coherence with real LLM-based coherence check (Task 5)
        step_descriptions = [s.description for s in state.steps if s.description]
        coherence = await self._score_coherence(state.goal, step_descriptions, provider)
        scorecard.scores["coherence"] = coherence
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

        Calls ``score_async`` (which includes LLM-based coherence scoring) and
        then writes the result to the ``evaluations`` DB table so scores survive
        restarts and are queryable for dashboards / self-optimisation.
        """
        # Score with LLM-based coherence (replaces heuristic)
        scorecard = await self.score_async(state=state, tenant_ctx=tenant_ctx, provider=provider)
        # Persist to DB
        if db is not None:
            try:
                import uuid

                from sqlalchemy import text
                async with db() as session, session.begin():
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
