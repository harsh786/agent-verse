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

        # H-2: SelfOptimizerV2 arm config injection — pick experiment arm for this run
        self_opt_v2 = (
            getattr(self._app_state, "self_optimizer_v2", None) if self._app_state else None
        )
        if self_opt_v2 and self._agent_id:
            try:
                arm_config = await self_opt_v2.get_arm_config(
                    agent_id=self._agent_id,
                    goal_id=agent_state.goal_id,
                    tenant_id=tenant_ctx.tenant_id,
                )
                if arm_config and isinstance(agent_state.context, dict):
                    agent_state.context["_experiment_arm"] = arm_config.get("arm_name", "control")
            except Exception:
                pass

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
