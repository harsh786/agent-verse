"""Mixin extracted from app.agent.graph — zero semantic changes."""

from __future__ import annotations

from typing import Any

from app.agent.prompts import (
    CHAIN_OF_THOUGHT_SYSTEM,
    REFLECTION_SYSTEM,
)
from app.agent.state import AgentState, StepStatus
from app.providers.base import CompletionRequest, Message
from app.providers.circuit_breaker import call_with_circuit_breaker

# Guardrails 2.0 integration
try:
    from app.guardrails_v2.engine import guardrails_engine
    from app.guardrails_v2.models import GuardrailLayer

    _GUARDRAILS_AVAILABLE = True
except ImportError:
    _GUARDRAILS_AVAILABLE = False
    guardrails_engine = None  # type: ignore[assignment]
    GuardrailLayer = None  # type: ignore[assignment]

import contextlib

from app.agent.graph_types import GraphState, RetrievalEntryPointError  # noqa: F401


class ReasoningMixin:
    """Mixin: CoT/reflection nodes (think, reflect, self_consistency, tree_of_thoughts, peer_review, supervisor, debate, refine)."""

    async def _node_refine(self, state: GraphState) -> dict:
        """Self-Refine node — improves last step output before verification (doc-1 §3.4).

        Different from Reflection (which diagnoses a FAILURE).
        Self-Refine improves a SUCCESS — makes a good output better.

        Activated when 'self_refine' is in PatternConfig.reasoning_patterns.
        Fires AFTER execute, BEFORE verify, at most max_refine_iterations times.
        """
        agent_state = state.get("agent_state")
        if agent_state is None:
            return {}

        try:
            from app.agent.prompts import SELF_REFINE_SYSTEM
            from app.providers.base import CompletionRequest, Message

            if not agent_state.steps:
                return {"agent_state": agent_state}

            last_step = agent_state.steps[-1]
            if not last_step.output or not last_step.output.strip():
                return {"agent_state": agent_state}

            refine_iterations = agent_state.context.get("refine_iterations", 0)
            max_refine = agent_state.context.get("max_refine_iterations", 2)
            if refine_iterations >= max_refine:
                return {"agent_state": agent_state}

            refine_prompt = (
                f"Task: {last_step.description}\n\n"
                f"Current output:\n{last_step.output[:2000]}\n\n"
                "Improve this output following the review checklist."
            )

            resp = await self._executor.complete(
                CompletionRequest(
                    messages=[
                        Message(role="system", content=SELF_REFINE_SYSTEM),
                        Message(role="user", content=refine_prompt),
                    ],
                    model="",
                    max_tokens=2000,
                    temperature=0.0,
                )
            )

            refined = resp.content.strip() if resp.content else ""
            if refined and not refined.startswith("NO_CHANGES_NEEDED"):
                last_step.output = refined
                agent_state.context["refine_iterations"] = refine_iterations + 1
            evidence_status = "completed" if refined else "degraded"
            agent_state.context.setdefault("reasoning_evidence", []).append(
                {
                    "strategy_id": "self_refine",
                    "adapter_version": "1.0.0",
                    "status": evidence_status,
                    "call_count": 1,
                    "round": refine_iterations + 1,
                    "changed": bool(refined and not refined.startswith("NO_CHANGES_NEEDED")),
                }
            )

        except Exception as exc:
            try:
                from app.observability.logging import get_logger

                get_logger(__name__).warning("node_refine_failed", error=str(exc))
            except Exception:
                pass

        return {"agent_state": agent_state}

    async def _node_think(self, state: GraphState) -> dict[str, Any]:
        """Chain-of-thought thinking node: produces reasoning before planning."""
        agent_state: AgentState = state["agent_state"]
        req = CompletionRequest(
            messages=[
                Message(role="system", content=CHAIN_OF_THOUGHT_SYSTEM),
                Message(role="user", content=f"Goal: {agent_state.goal}"),
            ],
            model=(self._model_router.model_for("think") if self._model_router is not None else ""),
        )
        try:
            resp = await call_with_circuit_breaker(
                self._planner,
                "complete",
                req,
                provider_name=type(self._planner).__name__,
            )
        except RuntimeError as cb_exc:
            raise PermissionError(f"Planning unavailable: {cb_exc}") from cb_exc
        # The provider's private reasoning is intentionally discarded. Only
        # aggregate execution evidence is checkpointed or exposed.
        agent_state.context.setdefault("reasoning_evidence", []).append(
            {
                "strategy_id": "chain_of_thought",
                "adapter_version": "1.0.0",
                "status": "completed" if resp.content else "degraded",
                "call_count": 1,
                "safe_rationale_summary": "deliberate reasoning phase completed",
            }
        )
        return {
            "agent_state": agent_state,
            "reasoning_evidence": agent_state.context["reasoning_evidence"][-1],
        }

    async def _node_reflect(self, state: GraphState) -> dict[str, Any]:
        """Reflection node: diagnoses failure and populates verification_feedback."""
        agent_state: AgentState = state["agent_state"]
        reflection_attempts = int(agent_state.context.get("reflection_attempts", 0))
        max_reflections = self._max_reflection_rounds()
        if reflection_attempts >= max_reflections:
            agent_state.context.setdefault("reasoning_evidence", []).append(
                {
                    "strategy_id": "reflection",
                    "adapter_version": "1.0.0",
                    "status": "exhausted",
                    "call_count": 0,
                    "limit_reason": "reflection_round_limit",
                }
            )
            return {"agent_state": agent_state}
        failed_steps = [s for s in agent_state.steps if s.status == StepStatus.FAILED]
        failed_summary = (
            "\n".join(f"- {s.description}: {s.error}" for s in failed_steps)
            or agent_state.error_message
            or "No specific failure information available."
        )
        _reflect_model = ""
        if self._model_router is not None:
            with contextlib.suppress(Exception):
                _reflect_model = self._model_router.model_for("reflection") or ""
        req = CompletionRequest(
            messages=[
                Message(role="system", content=REFLECTION_SYSTEM),
                Message(
                    role="user",
                    content=f"Goal: {agent_state.goal}\nFailed steps:\n{failed_summary}",
                ),
            ],
            model=_reflect_model,
        )
        try:
            resp = await call_with_circuit_breaker(
                self._planner,
                "complete",
                req,
                provider_name=type(self._planner).__name__,
            )
        except RuntimeError as cb_exc:
            raise PermissionError(f"Planning unavailable: {cb_exc}") from cb_exc
        from app.agent.reasoning_evidence import critique_categories

        categories = critique_categories(resp.content or "")
        agent_state.context.setdefault(
            "original_verification_evidence", agent_state.verification_feedback
        )
        agent_state.context["reflection_attempts"] = reflection_attempts + 1
        agent_state.verification_feedback = "Reflection identified categories: " + ", ".join(
            categories
        )
        agent_state.context.setdefault("reasoning_evidence", []).append(
            {
                "strategy_id": "reflection",
                "adapter_version": "1.0.0",
                "status": "completed",
                "call_count": 1,
                "critique_categories": list(categories),
                "attempt": reflection_attempts + 1,
            }
        )
        return {"agent_state": agent_state}

    # ------------------------------------------------------------------
    # H7: Advanced reasoning pattern nodes
    # ------------------------------------------------------------------

    async def _node_self_consistency(self, state: GraphState) -> dict[str, Any]:
        """Self-Consistency: sample N responses, return majority-vote answer."""
        agent_state: AgentState = state.get("agent_state")
        if agent_state is None or not agent_state.steps:
            return {}
        try:
            from app.agent.patterns.self_consistency import SelfConsistencyPattern

            last_step = agent_state.steps[-1]
            if not last_step.output:
                return {"agent_state": agent_state}
            pattern = SelfConsistencyPattern(n_samples=3)
            execution = await pattern.execute_with_evidence(
                prompt=f"Goal: {agent_state.goal}\nCurrent answer: {last_step.output}",
                provider=self._executor,
                call_limit=self.runtime_profile.effective_limits.calls
                if self.runtime_profile is not None
                else None,
            )
            refined = str(execution.result)
            agent_state.context.setdefault("reasoning_evidence", []).append(
                execution.evidence.model_dump(mode="json")
            )
            if refined and refined != last_step.output:
                last_step.output = refined
                agent_state.context["self_consistency_applied"] = True
        except Exception as exc:
            try:
                from app.observability.logging import get_logger

                get_logger(__name__).warning("node_self_consistency_failed", error=str(exc))
            except Exception:
                pass
        return {"agent_state": agent_state}

    async def _node_tree_of_thoughts(self, state: GraphState) -> dict[str, Any]:
        """Tree of Thoughts: deliberate reasoning over solution space."""
        agent_state: AgentState = state.get("agent_state")
        if agent_state is None:
            return {}
        try:
            from app.agent.patterns.tree_of_thoughts import TreeOfThoughtsPattern

            pattern = TreeOfThoughtsPattern(n_thoughts=3, max_depth=2)
            execution = await pattern.execute_with_evidence(
                problem=agent_state.goal,
                provider=self._planner,
            )
            answer = str(execution.result)
            agent_state.context.setdefault("reasoning_evidence", []).append(
                execution.evidence.model_dump(mode="json")
            )
            if answer:
                agent_state.context["tot_answer"] = answer
                agent_state.context["tree_of_thoughts_applied"] = True
        except Exception as exc:
            try:
                from app.observability.logging import get_logger

                get_logger(__name__).warning("node_tree_of_thoughts_failed", error=str(exc))
            except Exception:
                pass
        return {"agent_state": agent_state}

    async def _node_peer_review(self, state: GraphState) -> dict[str, Any]:
        """Peer Review: independent LLM review of current output quality."""
        agent_state: AgentState = state.get("agent_state")
        if agent_state is None or not agent_state.steps:
            return {}
        try:
            from app.agent.patterns.peer_review import PeerReviewPattern

            last_step = agent_state.steps[-1]
            if not last_step.output:
                return {"agent_state": agent_state}
            pattern = PeerReviewPattern(quality_threshold=0.7)
            role_assignments = (
                dict(self.runtime_profile.model_role_assignments)
                if self.runtime_profile is not None
                else {}
            )
            # Legacy graphs still have distinct executor/verifier roles even
            # when no runtime profile supplies concrete model identities.
            # Preserve that logical separation; explicit profiles remain the
            # source of truth and can reject an actual same-model assignment.
            producer_identity = role_assignments.get("executor", "executor-role")
            reviewer_identity = role_assignments.get("reviewer", "verifier-role")
            execution = await pattern.execute_with_evidence(
                output=last_step.output,
                goal=agent_state.goal,
                provider=self._verifier,
                producer_identity=producer_identity,
                reviewer_identity=reviewer_identity,
            )
            review = execution.result
            agent_state.context.setdefault("reasoning_evidence", []).append(
                execution.evidence.model_dump(mode="json")
            )
            agent_state.context["peer_review_score"] = review.quality_score
            agent_state.context["peer_review_approved"] = review.approved
            if not review.approved:
                agent_state.verification_feedback = (
                    f"Peer review rejected output at score {review.quality_score:.2f}; "
                    f"categories: {', '.join(execution.evidence.critique_categories)}"
                )
                agent_state.verification_success = False
        except Exception as exc:
            try:
                from app.observability.logging import get_logger

                get_logger(__name__).warning("node_peer_review_failed", error=str(exc))
            except Exception:
                pass
        return {"agent_state": agent_state}

    # ------------------------------------------------------------------
    # H8: Supervisor / debate node stubs
    # ------------------------------------------------------------------

    async def _node_supervisor_check(self, state: GraphState) -> dict[str, Any]:
        """Supervisor check stub — delegates to app.agent.supervisor when available."""
        agent_state: AgentState = state.get("agent_state")
        try:
            from app.observability.logging import get_logger

            get_logger(__name__).warning(
                "supervisor_node_stub_invoked",
                goal_id=getattr(agent_state, "goal_id", None),
            )
        except Exception:
            pass
        return {"agent_state": agent_state} if agent_state is not None else {}

    async def _node_debate(self, state: GraphState) -> dict[str, Any]:
        """Debate node stub — delegates to app.agent.debate when available."""
        agent_state: AgentState = state.get("agent_state")
        try:
            from app.observability.logging import get_logger

            get_logger(__name__).warning(
                "debate_node_stub_invoked",
                goal_id=getattr(agent_state, "goal_id", None),
            )
        except Exception:
            pass
        return {"agent_state": agent_state} if agent_state is not None else {}
