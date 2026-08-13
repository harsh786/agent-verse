"""Canonical profile-before-compile factory for the local AgentGraph kernel."""

from __future__ import annotations

from typing import Any

from app.agent.graph import AgentGraph
from app.orchestration.runtime_profile import GoalRuntimeProfile
from app.orchestration.strategy_adapters import ExecutionTier


class GraphFactory:
    def create(
        self,
        profile: GoalRuntimeProfile | None,
        services: dict[str, Any],
        agent_config: dict[str, Any] | None = None,
    ) -> AgentGraph:
        if profile is None:
            raise ValueError("runtime profile is required before graph compilation")
        if profile.profile_version != 2:
            raise ValueError("profile_version must be 2")
        if not profile.goal_id or not profile.tenant_id:
            raise ValueError("runtime profile must identify the exact goal and tenant")
        if profile.execution_tier is ExecutionTier.DISTRIBUTED:
            raise ValueError("distributed strategy requires StrategyRunner")
        missing = {"planner", "executor", "verifier"} - services.keys()
        if missing:
            raise ValueError(f"missing graph services: {sorted(missing)}")

        config = agent_config or {}
        selected_ids = {
            profile.primary_strategy.strategy_id,
            *(item.strategy_id for item in profile.auxiliary_strategies),
        }
        flags = {
            "enable_cot": "chain_of_thought" in selected_ids,
            "enable_reflection": "reflection" in selected_ids,
            "enable_self_refine": "self_refine" in selected_ids,
            "enable_self_consistency": "self_consistency" in selected_ids,
            "enable_tree_of_thoughts": "tree_of_thoughts" in selected_ids,
            "enable_peer_review": "peer_review" in selected_ids,
        }
        for flag_name, selected in tuple(flags.items()):
            if flag_name in config:
                flags[flag_name] = selected and bool(config[flag_name])
        graph_kwargs: dict[str, Any] = dict(services)
        graph_kwargs.update(flags)
        graph_kwargs["runtime_profile"] = profile
        return AgentGraph(**graph_kwargs)
