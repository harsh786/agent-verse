"""Mixin extracted from app.agent.graph — zero semantic changes."""

from __future__ import annotations

from typing import Any

from app.agent.sanitization import (
    sanitize_event,
    sanitize_event_value,
    sanitize_tool_event_value,
    sanitize_tool_raw_output,
)
from app.agent.state import AgentState, GoalStatus
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
from app.agent.nodes._helpers import _guardrail_should_fail_closed


class InitializeMixin:
    """Mixin: _sanitize_* helpers + _node_initialize."""

    def _sanitize_tool_raw_output(self, value: object) -> str:
        return sanitize_tool_raw_output(value, result_processor=self._result_processor)

    def _sanitize_tool_event_value(self, value: object) -> str:
        return sanitize_tool_event_value(value, result_processor=self._result_processor)

    def _sanitize_event_value(self, value: Any) -> Any:
        return sanitize_event_value(value, result_processor=self._result_processor)

    def _sanitize_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return sanitize_event(event, result_processor=self._result_processor)

    # ------------------------------------------------------------------
    # Nodes
    # ------------------------------------------------------------------

    # Agent-config keys an experiment arm can change that this graph really
    # applies per goal. system_prompt is read by the planner from context.
    _EXPERIMENT_APPLICABLE_KEYS: frozenset[str] = frozenset({"system_prompt"})

    async def _apply_experiment_arm(
        self, self_opt_v2: Any, agent_state: AgentState, tenant_ctx: TenantContext
    ) -> None:
        """Pick this goal's SelfOptimizerV2 arm and APPLY its config.

        Only the arm's name used to be stored (and even that was read from an
        ``arm_name`` key the config never has, so every goal was "control"); the
        candidate config was never applied, so the experiment compared two
        identical runs and its "winner" was noise. Now the candidate's changed
        keys are applied; an experiment whose change cannot be applied here is
        excluded (no arm recorded for either arm), rather than measured as noise.
        """
        ctx = agent_state.context
        try:
            get_assignment = getattr(self_opt_v2, "get_arm_assignment", None)
            if get_assignment is None:
                return
            assignment = await get_assignment(
                tenant_id=tenant_ctx.tenant_id,
                agent_id=self._agent_id,
                goal_id=agent_state.goal_id,
            )
        except Exception as exc:
            self._logger.warning("experiment_arm_assignment_failed", error=str(exc)[:200])
            return
        arm = str(assignment.get("arm") or "control")
        changed = [str(k) for k in assignment.get("changed_keys") or []]
        unapplicable = sorted(set(changed) - self._EXPERIMENT_APPLICABLE_KEYS)
        if unapplicable:
            ctx["_experiment_excluded"] = {
                "experiment_id": assignment.get("experiment_id"),
                "reason": "arm changes config keys this runtime cannot apply per goal",
                "keys": unapplicable,
            }
            self._logger.warning(
                "experiment_arm_not_applicable",
                experiment_id=assignment.get("experiment_id"),
                keys=unapplicable,
            )
            return
        if arm != "control":
            config = assignment.get("config") or {}
            applied: list[str] = []
            if "system_prompt" in changed and isinstance(config.get("system_prompt"), str):
                ctx["system_prompt"] = config["system_prompt"]
                applied.append("system_prompt")
            ctx["_experiment_arm_applied_keys"] = applied
        ctx["_experiment_arm"] = arm

    async def _node_initialize(self, state: GraphState) -> dict[str, Any]:
        goal: str = state["goal"]
        tenant_ctx: TenantContext = state["tenant_ctx"]
        existing_state = state.get("agent_state")
        agent_state = (
            existing_state
            if isinstance(existing_state, AgentState)
            else AgentState(goal=goal, tenant_ctx=tenant_ctx)
        )
        if not agent_state.goal:
            agent_state.goal = goal
        if agent_state.tenant_ctx is None:
            agent_state.tenant_ctx = tenant_ctx
        await self._emit({"type": "goal_started", "goal": goal})

        # Guardrail: check goal for injection attempts
        if self._guardrail_checker is not None:
            goal_issues = self._guardrail_checker.check_goal(goal=agent_state.goal)
            if goal_issues:
                agent_state.status = GoalStatus.FAILED
                agent_state.error_message = f"Goal rejected by guardrails: {'; '.join(goal_issues)}"
                await self._emit({"type": "goal_rejected", "reason": agent_state.error_message})
                return {"agent_state": agent_state, "terminal_reason": "guardrail_rejected"}

        # Guardrails 2.0: GOAL layer — declared in GuardrailLayer but never
        # actually checked anywhere before this fix, so a compliance-bundle
        # rule targeting "goal" (e.g. HIPAA's PHI rule, the SOC2 prompt-
        # injection rule) had zero real effect. Mirrors the FINAL_OUTPUT /
        # TOOL_ARGS block pattern in guardrail_enforcer.py: seed baseline
        # rules (idempotent), evaluate, and reject the goal before planning
        # ever starts on a BLOCK verdict. Runs in addition to (not instead
        # of) the legacy ``_guardrail_checker.check_goal`` above.
        if _GUARDRAILS_AVAILABLE and guardrails_engine is not None:
            try:
                guardrails_engine.ensure_default_rules(tenant_ctx.tenant_id)
                _g2_goal_result = await guardrails_engine.evaluate(
                    content=agent_state.goal[:2000],
                    layer=GuardrailLayer.GOAL,
                    tenant_id=tenant_ctx.tenant_id,
                    goal_id=agent_state.goal_id,
                )
                if _g2_goal_result.get("blocked"):
                    agent_state.status = GoalStatus.FAILED
                    agent_state.error_message = (
                        "Goal rejected by guardrail policy (Guardrails 2.0)"
                    )
                    await self._emit(
                        {"type": "goal_rejected", "reason": agent_state.error_message}
                    )
                    return {
                        "agent_state": agent_state,
                        "terminal_reason": "guardrail_rejected",
                    }
            except Exception as _g2_goal_exc:
                # SAFE-4 (P0-15): an errored safety check must not read as
                # "allowed" on high-risk work — fail closed.
                if _guardrail_should_fail_closed(agent_state.goal, None):
                    agent_state.status = GoalStatus.FAILED
                    agent_state.error_message = (
                        "Goal guardrail check errored on high-risk goal; failing closed."
                    )
                    self._logger.warning(
                        "initialize_guardrail_failed_closed", error=str(_g2_goal_exc)
                    )
                    await self._emit(
                        {"type": "goal_rejected", "reason": agent_state.error_message}
                    )
                    return {
                        "agent_state": agent_state,
                        "terminal_reason": "guardrail_rejected",
                    }

        # H-2: SelfOptimizerV2 arm config injection — pick experiment arm for this run
        self_opt_v2 = (
            getattr(self._app_state, "self_optimizer_v2", None) if self._app_state else None
        )
        if self_opt_v2 and self._agent_id and isinstance(agent_state.context, dict):
            await self._apply_experiment_arm(self_opt_v2, agent_state, tenant_ctx)

        if self._runtime_profile is not None:
            agent_state.context["_runtime_profile"] = self._runtime_profile

        # H23-H26: Security profiles — compute per-goal identity + action safety context
        try:
            from app.security_runtime.governance_profile import GovernanceProfileSelector
            from app.security_runtime.identity_profile import IdentityResolver

            _id_resolver = IdentityResolver()
            _identity = _id_resolver.resolve(tenant_ctx=agent_state.tenant_ctx)
            agent_state.context["_identity_scope"] = _identity.identity_scope.value

            _runtime_profile_ctx = agent_state.context.get("_runtime_profile")
            if _runtime_profile_ctx is not None:
                _gov_selector = GovernanceProfileSelector()
                _gov_profile = _gov_selector.select(
                    _runtime_profile_ctx, tenant_ctx=agent_state.tenant_ctx
                )
                agent_state.context["_governance_bundle"] = _gov_profile.name.value
        except Exception:
            pass

        # Build source inventory for planner awareness (M1d)
        try:
            from app.rag.agentic.source_inventory import SourceInventory

            _kb = getattr(self, "_knowledge_store", None)
            if _kb is not None:
                inventory = SourceInventory(knowledge_store=_kb)
                sources = await inventory.build(tenant_ctx=agent_state.tenant_ctx)
                agent_state.context["_source_inventory"] = sources.to_dict()
        except Exception:
            pass

        return {"agent_state": agent_state, "iteration": 0, "rag_context": ""}
