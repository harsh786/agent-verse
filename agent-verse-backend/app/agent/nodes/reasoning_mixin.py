"""Mixin extracted from app.agent.graph — zero semantic changes."""

from __future__ import annotations

from typing import Any

from app.agent.nodes.llm_cost import ChargingProvider, charge_llm_call
from app.agent.prompts import (
    CHAIN_OF_THOUGHT_SYSTEM,
    REFLECTION_SYSTEM,
)
from app.agent.state import AgentState, StepStatus
from app.providers.base import CompletionRequest, Message
from app.providers.circuit_breaker import complete_with_failover

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
    """Mixin: CoT/reflection nodes (think, reflect, self_consistency, tree_of_thoughts, peer_review, supervisor, debate, refine)."""  # noqa: E501

    def _charging(self, provider: Any, role: str, agent_state: AgentState) -> Any:
        """Wrap *provider* so pattern-internal LLM calls are charged to the goal/tenant."""
        return ChargingProvider(
            provider,
            graph=self,
            role=role,
            agent_state=agent_state,
            tenant_ctx=agent_state.tenant_ctx,
        )

    @staticmethod
    def _is_tool_grounded(step: Any) -> bool:
        """True when the step's output came from real tool calls.

        Such output is the evidence the verifier/grounding gate checks; an LLM
        rewrite of it (self-refine / self-consistency) must never replace it,
        or the verifier ends up grading ungrounded text as if it were the tool
        result.
        """
        return bool(getattr(step, "tool_calls", None))

    async def _record_pattern_failure(
        self, agent_state: AgentState, pattern: str, exc: BaseException
    ) -> None:
        """Leave evidence that an optional reasoning pattern failed and was skipped.

        The error used to be only logged, so the goal trace could not tell a
        skipped pattern from one that ran (CORE-05). Never raises.
        """
        entry = {
            "pattern": pattern,
            "error_type": type(exc).__name__,
            "error": str(exc)[:200],
        }
        with contextlib.suppress(Exception):
            agent_state.context.setdefault("patterns_failed", []).append(entry)
            agent_state.context.setdefault("reasoning_evidence", []).append(
                {
                    "strategy_id": pattern,
                    "adapter_version": "1.0.0",
                    "status": "failed",
                    "call_count": 0,
                    "error_class": type(exc).__name__,
                }
            )
        with contextlib.suppress(Exception):
            from app.observability.logging import get_logger

            get_logger(__name__).warning(f"node_{pattern}_failed", error=str(exc))
        emit = getattr(self, "_emit", None)
        if emit is not None:
            with contextlib.suppress(Exception):
                await emit(
                    {
                        "type": "pattern_failed",
                        "pattern": pattern,
                        "error_type": type(exc).__name__,
                    }
                )

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
            if self._is_tool_grounded(last_step):
                agent_state.context.setdefault("reasoning_evidence", []).append(
                    {
                        "strategy_id": "self_refine",
                        "adapter_version": "1.0.0",
                        "status": "skipped",
                        "call_count": 0,
                        "limit_reason": "grounded_tool_output",
                    }
                )
                return {"agent_state": agent_state}
            _refine_model = ""
            if self._model_router is not None:
                with contextlib.suppress(Exception):
                    _refine_model = (
                        self._model_router.model_for("refine")
                        or self._model_router.model_for("execute")
                        or ""
                    )

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
                    model=_refine_model,
                    max_tokens=2000,
                    temperature=0.0,
                )
            )

            await charge_llm_call(
                self,
                resp=resp,
                role="refine",
                model=_refine_model,
                agent_state=agent_state,
                tenant_ctx=agent_state.tenant_ctx,
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
            # The step output is left untouched; record the failure as evidence
            # so the run does not claim self-refine was applied.
            agent_state.context.setdefault("reasoning_evidence", []).append(
                {
                    "strategy_id": "self_refine",
                    "adapter_version": "1.0.0",
                    "status": "failed",
                    "call_count": 0,
                    "error_class": type(exc).__name__,
                }
            )
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
            resp = await complete_with_failover(
                self._planner, req, fallback_models=self._role_fallback_models()
            )
        except (RuntimeError, TimeoutError) as cb_exc:
            raise PermissionError(f"Planning unavailable: {cb_exc}") from cb_exc
        await charge_llm_call(
            self,
            resp=resp,
            role="think",
            model=req.model,
            agent_state=agent_state,
            tenant_ctx=state.get("tenant_ctx") or agent_state.tenant_ctx,
        )
        # The reasoning is handed to the planner through a transient, per-goal
        # in-memory slot (consumed by _node_plan), so it is never checkpointed or
        # exposed — but it is no longer thrown away unused. Only aggregate
        # execution evidence is checkpointed.
        if resp.content and resp.content.strip():
            transient = getattr(self, "_transient_reasoning", None)
            if not isinstance(transient, dict):
                transient = {}
                self._transient_reasoning = transient
            transient[str(agent_state.goal_id or id(agent_state))] = resp.content.strip()[:4000]
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
            resp = await complete_with_failover(
                self._planner, req, fallback_models=self._role_fallback_models()
            )
        except (RuntimeError, TimeoutError) as cb_exc:
            raise PermissionError(f"Planning unavailable: {cb_exc}") from cb_exc
        await charge_llm_call(
            self,
            resp=resp,
            role="reflection",
            model=_reflect_model,
            agent_state=agent_state,
            tenant_ctx=state.get("tenant_ctx") or agent_state.tenant_ctx,
        )
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
            if self._is_tool_grounded(last_step):
                agent_state.context.setdefault("reasoning_evidence", []).append(
                    {
                        "strategy_id": "self_consistency",
                        "adapter_version": "1.0.0",
                        "status": "skipped",
                        "call_count": 0,
                        "limit_reason": "grounded_tool_output",
                    }
                )
                return {"agent_state": agent_state}
            pattern = SelfConsistencyPattern(n_samples=3)
            execution = await pattern.execute_with_evidence(
                prompt=f"Goal: {agent_state.goal}\nCurrent answer: {last_step.output}",
                provider=self._charging(self._executor, "self_consistency", agent_state),
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
            await self._record_pattern_failure(agent_state, "self_consistency", exc)
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
                provider=self._charging(self._planner, "tree_of_thoughts", agent_state),
            )
            answer = str(execution.result)
            agent_state.context.setdefault("reasoning_evidence", []).append(
                execution.evidence.model_dump(mode="json")
            )
            if answer:
                agent_state.context["tot_answer"] = answer
                agent_state.context["tree_of_thoughts_applied"] = True
        except Exception as exc:
            await self._record_pattern_failure(agent_state, "tree_of_thoughts", exc)
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
                provider=self._charging(self._verifier, "peer_review", agent_state),
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
            await self._record_pattern_failure(agent_state, "peer_review", exc)
        return {"agent_state": agent_state}

    # ------------------------------------------------------------------
    # H8 / D-1 / D-2: Supervisor and debate multi-agent pattern nodes
    # ------------------------------------------------------------------

    async def _record_pattern_decision(
        self, agent_state: AgentState, decision: dict[str, Any]
    ) -> None:
        """Record a pattern-selection decision into state and emit an SSE event.

        Makes pattern execution observable (D-2): every time a supervisor/debate
        node runs it appends a structured record to ``context['pattern_decisions']``
        and, when an event callback is wired, emits it as a decision-trace event.
        """
        agent_state.context.setdefault("pattern_decisions", []).append(decision)
        emit = getattr(self, "_emit", None)
        if emit is not None:
            with contextlib.suppress(Exception):
                await emit({"type": "pattern_decision", **decision})

    async def _node_supervisor_check(self, state: GraphState) -> dict[str, Any]:
        """Supervisor node — runs the real SupervisorAgent decomposition pattern.

        Decomposes the goal into sub-tasks, dispatches them across sub-agents via
        the wired GoalService, and folds the synthesized result into state so the
        planner can build on it. Defensive: never crashes the graph; runs at most
        once per goal (guards replan loops); no-ops when no GoalService is wired
        (which would make sub-agent dispatch impossible).
        """
        agent_state: AgentState = state.get("agent_state")
        if agent_state is None:
            return {}
        if agent_state.context.get("supervisor_applied"):
            return {"agent_state": agent_state}
        from app.agent.supervisor import SUBGOAL_MARKER

        # A goal the supervisor spawned never decomposes again (no recursion).
        if agent_state.context.get(SUBGOAL_MARKER):
            return {"agent_state": agent_state}
        goal_service = getattr(self, "_goal_service", None)
        if goal_service is None:
            return {"agent_state": agent_state}
        try:
            from app.agent.supervisor import SupervisorAgent

            tenant_ctx = getattr(agent_state, "tenant_ctx", None) or getattr(
                self, "_tenant_ctx_ref", None
            )
            supervisor = SupervisorAgent(
                planner_provider=self._charging(self._planner, "supervisor", agent_state),
                goal_service=goal_service,
                agent_router=getattr(self, "_agent_router", None),
            )
            result = await supervisor.run(
                goal=agent_state.goal,
                tenant_ctx=tenant_ctx,
                event_callback=getattr(self, "_event_callback", None),
                parent_goal_id=getattr(agent_state, "goal_id", None),
            )
            agent_state.context["supervisor_applied"] = True
            synthesized = getattr(result, "synthesized_result", "") or ""
            if synthesized:
                agent_state.context["supervisor_result"] = synthesized
            await self._record_pattern_decision(
                agent_state,
                {
                    "pattern": "supervisor",
                    "goal_id": getattr(agent_state, "goal_id", None),
                    "success": bool(getattr(result, "success", False)),
                    "task_count": len(getattr(result, "tasks", []) or []),
                },
            )
        except Exception as exc:
            await self._record_pattern_failure(agent_state, "supervisor", exc)
        return {"agent_state": agent_state}

    async def _node_debate(self, state: GraphState) -> dict[str, Any]:
        """Debate node — runs the real DebateOrchestrator voting pattern.

        N agents independently propose, critique, and vote; the winning proposal is
        folded into state as deliberation context for the planner. Defensive: never
        crashes the graph; runs at most once per goal (guards replan loops).
        """
        agent_state: AgentState = state.get("agent_state")
        if agent_state is None:
            return {}
        if agent_state.context.get("debate_applied"):
            return {"agent_state": agent_state}
        from app.agent.supervisor import SUBGOAL_MARKER

        if agent_state.context.get(SUBGOAL_MARKER):
            return {"agent_state": agent_state}
        try:
            from app.agent.debate import DebateOrchestrator

            orchestrator = DebateOrchestrator(
                provider=self._charging(self._planner, "debate", agent_state)
            )
            result = await orchestrator.run(
                goal=agent_state.goal,
                context=str(agent_state.context.get("rag_context", "")),
                event_callback=getattr(self, "_event_callback", None),
            )
            agent_state.context["debate_applied"] = True
            winning = getattr(result, "winning_proposal", "") or ""
            if winning:
                agent_state.context["debate_result"] = winning
            await self._record_pattern_decision(
                agent_state,
                {
                    "pattern": "debate",
                    "goal_id": getattr(agent_state, "goal_id", None),
                    "winning_agent": getattr(result, "winning_agent", ""),
                    "consensus_level": float(getattr(result, "consensus_level", 0.0)),
                },
            )
        except Exception as exc:
            await self._record_pattern_failure(agent_state, "debate", exc)
        return {"agent_state": agent_state}
