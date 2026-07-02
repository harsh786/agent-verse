"""Eval runner — scores completed goals on 7 dimensions."""
from __future__ import annotations

import time
from typing import Any

from app.agent.state import AgentState, GoalStatus
from app.intelligence.eval import EvalScorecard
from app.tenancy.context import TenantContext


class EvalRunner:
    """Scores a completed AgentState on the 7 evaluation dimensions."""

    def _score_tool_relevance(self, steps: list, iterations: int) -> float:
        """Score tool call efficiency: redundant/failed calls lower the score.

        Returns a float in [0.0, 1.0].
        - 0.5 when no step data (neutral)
        - 0.7 when steps exist but contain no tool calls
        - Higher for efficient, successful tool usage; lower for failures/redundancy
        """
        if not steps:
            return 0.5

        all_calls: list = []
        for s in steps:
            all_calls.extend(getattr(s, "tool_calls", None) or [])

        if not all_calls:
            return 0.7  # steps exist but no tool calls — mild positive

        # Count failed calls
        failed = sum(
            1 for tc in all_calls
            if isinstance(tc, dict)
            and (tc.get("error") or tc.get("status") == "failed")
        )
        total = len(all_calls)
        success_rate = max(0.0, 1.0 - failed / total)

        # Efficiency: ideal ~2 tool calls per step; penalize above that
        avg_per_step = total / max(len(steps), 1)
        efficiency = max(0.0, 1.0 - max(0.0, avg_per_step - 2.0) / 5.0)

        return 0.6 * success_rate + 0.4 * efficiency

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

        # 3. accuracy — heuristic placeholder (replaced by LLM scoring in score_async)
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
        if not state.steps:
            coherence = 0.5
        else:
            steps_with_output = sum(1 for s in state.steps if getattr(s, 'output', ''))
            output_rate = steps_with_output / len(state.steps)
            unique_descriptions = len({getattr(s, 'description', '') for s in state.steps})
            diversity = min(1.0, unique_descriptions / max(len(state.steps), 1))
            coherence = 0.6 * output_rate + 0.4 * diversity

        # 6. sla — did the goal complete within SLA budget?
        _ctx2 = getattr(state, "context", None)
        started_at = (
            float(_ctx2.get("execution_started_at", 0.0)) if isinstance(_ctx2, dict) else 0.0
        )
        sla_budget_s = (
            float(_ctx2.get("sla_budget_seconds", 300.0)) if isinstance(_ctx2, dict) else 300.0
        )
        if started_at > 1e6:  # valid monotonic timestamp
            duration_s = time.monotonic() - started_at
            sla_score = max(0.0, 1.0 - max(0.0, duration_s - sla_budget_s) / max(sla_budget_s, 1))
        elif state.iterations and state.iterations > 1:
            # Proxy: use iteration count as a time proxy — each iteration ~20s average
            estimated_duration_s = state.iterations * 20.0
            over_budget = max(0.0, estimated_duration_s - sla_budget_s)
            sla_score = max(0.0, 1.0 - over_budget / max(sla_budget_s, 1))
        else:
            sla_score = 1.0  # No timing or iteration data — default

        # 7. tool_relevance — NEW: efficiency/quality of tool usage
        tool_relevance = self._score_tool_relevance(state.steps, state.iterations)

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

    async def _score_accuracy(
        self,
        goal: str,
        steps: list,
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
        heuristic = (
            1.0 if verification_success
            else (0.5 if "partial" in feedback else 0.0)
        )
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
            resp = await provider.complete(CompletionRequest(
                messages=[Message(role="user", content=prompt)],
                model="",
                max_tokens=10,
            ))
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

        Calls ``score_async`` (which includes LLM-based coherence + accuracy scoring) and
        then writes the result to the ``evaluations`` DB table so scores survive
        restarts and are queryable for dashboards / self-optimisation.

        The INSERT tries to include score_tool_relevance; if that column does not yet
        exist (older schema), it falls back to an INSERT without that column.
        """
        # Score with LLM-based coherence + accuracy (replaces heuristics)
        scorecard = await self.score_async(state=state, tenant_ctx=tenant_ctx, provider=provider)
        # Persist to DB
        if db is not None:
            import uuid

            from sqlalchemy import text

            eval_id = uuid.uuid4().hex
            params: dict = {
                "id": eval_id,
                "gid": state.goal_id,
                "tid": tenant_ctx.tenant_id,
                "tc": scorecard.scores.get("task_completion", 0.0),
                "eff": scorecard.scores.get("efficiency", 0.0),
                "acc": scorecard.scores.get("accuracy", 0.0),
                "saf": scorecard.scores.get("safety", 0.0),
                "coh": scorecard.scores.get("coherence", 0.0),
                "sla": scorecard.scores.get("sla", 1.0),
                "tr": scorecard.scores.get("tool_relevance", 0.5),
                "passed": scorecard.passed(),
            }
            try:
                async with db() as session, session.begin():
                    await session.execute(
                        text("""INSERT INTO evaluations
                            (id, goal_id, tenant_id,
                             score_task_completion, score_efficiency,
                             score_accuracy, score_safety, score_coherence,
                             score_sla, score_tool_relevance, passed, run_at)
                            VALUES
                            (:id, :gid, :tid, :tc, :eff, :acc, :saf,
                             :coh, :sla, :tr, :passed, NOW())
                            ON CONFLICT DO NOTHING"""),
                        params,
                    )
            except Exception:
                # score_tool_relevance column may not exist in older schemas — retry without it
                try:
                    async with db() as session, session.begin():
                        await session.execute(
                            text("""INSERT INTO evaluations
                                (id, goal_id, tenant_id,
                                 score_task_completion, score_efficiency,
                                 score_accuracy, score_safety, score_coherence,
                                 score_sla, passed, run_at)
                                VALUES
                                (:id, :gid, :tid, :tc, :eff, :acc, :saf,
                                 :coh, :sla, :passed, NOW())
                                ON CONFLICT DO NOTHING"""),
                            params,
                        )
                except Exception as exc2:
                    from app.observability.logging import get_logger
                    get_logger(__name__).warning("eval_persist_failed", error=str(exc2))
        return scorecard
