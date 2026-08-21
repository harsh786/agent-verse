"""Mixin extracted from app.agent.graph — zero semantic changes."""

from __future__ import annotations

from app.agent.state import AgentState, GoalStatus
from app.observability.metrics import (
    record_goal_failed,
)
from app.tenancy.context import TenantContext

# Guardrails 2.0 integration
try:
    from app.guardrails_v2.engine import guardrails_engine
    from app.guardrails_v2.models import GuardrailLayer

    _GUARDRAILS_AVAILABLE = True
except ImportError:
    _GUARDRAILS_AVAILABLE = False
    guardrails_engine = None  # type: ignore[assignment]
    GuardrailLayer = None  # type: ignore[assignment]

from app.agent.graph_types import GraphState, RetrievalEntryPointError  # noqa: F401


class RoutingMixin:
    """Mixin: _max_reflection_rounds, _route, _route_after_execute."""

    def _max_reflection_rounds(self) -> int:
        return max(
            0,
            min(
                2,
                int(
                    getattr(
                        getattr(self._runtime_profile, "effective_limits", None),
                        "rounds",
                        2,
                    )
                ),
            ),
        )

    def _route(self, state: GraphState) -> str:
        agent_state: AgentState | None = state.get("agent_state")
        if agent_state is None:
            return "max_iter"

        if state.get("terminal_reason") == "guardrail_rejected":
            return "max_iter"  # Terminate immediately

        if agent_state.verification_success:
            return "complete"

        # Bug 1 fix: when verifier says retry=False, permanently fail rather than replan
        _v_retry: bool = bool(agent_state.context.get("verification_retry", True))
        if not _v_retry:
            agent_state.status = GoalStatus.FAILED
            agent_state.error_message = (
                agent_state.verification_feedback or "Goal permanently failed: cannot be retried."
            )
            record_goal_failed(tenant_id=agent_state.tenant_ctx.tenant_id)
            return "max_iter"

        iteration: int = state.get("iteration", 0)
        if iteration >= self._max_iterations:
            agent_state.status = GoalStatus.FAILED
            agent_state.error_message = (
                f"Goal failed: max iterations ({self._max_iterations}) reached."
            )
            record_goal_failed(tenant_id=agent_state.tenant_ctx.tenant_id)
            return "max_iter"

        # ── Stuck-loop detection ─────────────────────────────────────────────
        # If the last 3 steps in the *current* execution round are all FAILED,
        # force a replan rather than continuing the failing approach.
        try:
            if self._check_stuck_loop(agent_state, window=3):
                agent_state.context["_stuck_loop_replan"] = True
                self._logger.warning(
                    "stuck_loop_detected",
                    goal_id=agent_state.goal_id,
                    consecutive_failures=3,
                )
                # Emit is async; record intent in context and let the execute
                # node pick it up at start of next iteration via event_callback.
                agent_state.context["_pending_events"] = [*agent_state.context.get("_pending_events", []), {"type": "stuck_loop_detected", "goal_id": agent_state.goal_id, "message": "3 consecutive step failures — forcing replan"}]  # noqa: E501
                return "replan"
        except Exception:
            pass  # never crash routing

        # ── Stagnation detection ────────────────────────────────────────────
        # If the last 3 verification feedbacks are identical (agent keeps making
        # the same mistake) — stop immediately rather than burning all iterations.
        _feedback_history: list[str] = agent_state.context.get("_feedback_history", [])
        _current_feedback = agent_state.verification_feedback or ""
        if _current_feedback:
            _feedback_history = ([*_feedback_history, _current_feedback])[-6:]
            agent_state.context["_feedback_history"] = _feedback_history

        if len(_feedback_history) >= 3 and len(set(_feedback_history[-3:])) == 1:
            agent_state.status = GoalStatus.FAILED
            agent_state.error_message = (
                "Goal stagnated: agent repeated the same failing approach 3 times in a row. "
                f"Last feedback: {_current_feedback[:200]}"
            )
            record_goal_failed(tenant_id=agent_state.tenant_ctx.tenant_id)
            return "max_iter"

        # Also stop if plan steps haven't changed for 3 iterations
        # (same plan, same failure = wrong agent/tools)
        _plan_history: list[str] = agent_state.context.get("_plan_history", [])
        _current_plan_key = "|".join(agent_state.plan[:3]) if agent_state.plan else ""
        if _current_plan_key:
            _plan_history = ([*_plan_history, _current_plan_key])[-4:]
            agent_state.context["_plan_history"] = _plan_history

        if len(_plan_history) >= 3 and len(set(_plan_history[-3:])) == 1:
            agent_state.status = GoalStatus.FAILED
            agent_state.error_message = (
                "Goal stagnated: same plan was repeated 3 times without success. "
                "Check that the agent has the right connectors for this goal."
            )
            record_goal_failed(tenant_id=agent_state.tenant_ctx.tenant_id)
            return "max_iter"

        # Supervised mode: pause if any HITL requests are pending
        if self._autonomy_mode == "supervised" and self._hitl_gateway is not None:
            tenant_ctx: TenantContext | None = state.get("tenant_ctx")
            if tenant_ctx is not None:
                pending = self._hitl_gateway.list_pending(
                    tenant_ctx=tenant_ctx, goal_id=agent_state.goal_id
                )
                if pending:
                    agent_state.status = GoalStatus.WAITING_HUMAN
                    return "waiting_human"

        # Context-gap detection (doc-2 §9.3) — route to rag_remediate before replanning
        try:
            from app.rag.agentic.context_gap_detector import ContextGapDetector

            _gap_detector = ContextGapDetector()
            _remediation_count = agent_state.context.get("remediation_count", 0)
            if (
                agent_state.context.get("allow_rag_remediation", False)
                and not agent_state.verification_success
                and _gap_detector.has_gap(agent_state.verification_feedback or "")
                and _remediation_count < 2
            ):
                return "rag_remediate"
        except Exception:
            pass  # never crash routing

        # This router only runs after verification, so a negative verification
        # result is itself sufficient evidence to enter the bounded reflection
        # path even when the verifier omitted explanatory text.
        if self._enable_reflection:
            reflection_attempts = int(agent_state.context.get("reflection_attempts", 0))
            if reflection_attempts < self._max_reflection_rounds():
                return "reflect"
            evidence = {
                "strategy_id": "reflection",
                "adapter_version": "1.0.0",
                "status": "exhausted",
                "call_count": 0,
                "limit_reason": "reflection_round_limit",
                "attempt": reflection_attempts,
            }
            prior = agent_state.context.setdefault("reasoning_evidence", [])
            if not prior or prior[-1] != evidence:
                prior.append(evidence)
            agent_state.context["terminal_reason"] = "reflection_exhausted"
            agent_state.status = GoalStatus.FAILED
            agent_state.error_message = "Reflection round limit exhausted."
            record_goal_failed(tenant_id=agent_state.tenant_ctx.tenant_id)
            return "max_iter"
        return "replan"

    def _route_after_execute(self, state: GraphState) -> str:
        agent_state: AgentState = state["agent_state"]
        return "failed" if agent_state.status is GoalStatus.FAILED else "continue"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------
