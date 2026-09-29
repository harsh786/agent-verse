"""Canonical profile-before-compile factory for the local AgentGraph kernel."""

from __future__ import annotations

from typing import Any

from app.agent.graph import AgentGraph
from app.orchestration.execution_drivers import AGENT_GRAPH_STRATEGY_FLAGS
from app.orchestration.runtime_profile import GoalRuntimeProfile
from app.orchestration.strategy_adapters import ExecutionTier


class GraphFactory:
    """Compile a runtime profile into an AgentGraph — only if the graph can honour it.

    Every strategy the profile names must map to an AgentGraph node/flag
    (``AGENT_GRAPH_STRATEGY_FLAGS``). A primary or auxiliary the kernel cannot run is a
    ``ValueError`` rather than a plain AgentGraph that silently runs something else while
    the profile claims the requested pattern.
    """

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
        if profile.execution_tier is not ExecutionTier.LOCAL:
            raise ValueError(
                f"{profile.execution_tier.value} strategy has no AgentGraph driver: "
                f"{profile.primary_strategy.strategy_id}"
            )
        missing = {"planner", "executor", "verifier"} - services.keys()
        if missing:
            raise ValueError(f"missing graph services: {sorted(missing)}")

        selected_ids = [
            profile.primary_strategy.strategy_id,
            *(item.strategy_id for item in profile.auxiliary_strategies),
        ]
        undriven = [item for item in selected_ids if item not in AGENT_GRAPH_STRATEGY_FLAGS]
        if undriven:
            raise ValueError(f"no AgentGraph driver for strategies: {undriven}")

        config = agent_config or {}
        flags = {flag: False for flag in AGENT_GRAPH_STRATEGY_FLAGS.values() if flag is not None}
        for strategy_id in selected_ids:
            flag = AGENT_GRAPH_STRATEGY_FLAGS[strategy_id]
            if flag is not None:
                flags[flag] = True
        # An agent config can switch a profile-selected node OFF, never on.
        for flag_name, selected in tuple(flags.items()):
            if flag_name in config:
                flags[flag_name] = selected and bool(config[flag_name])
        # Goal-tree decomposition predates the profile and stays an agent-level switch too.
        if not flags["enable_goal_tree"] and bool(services.get("enable_goal_tree")):
            flags["enable_goal_tree"] = True
        graph_kwargs: dict[str, Any] = dict(services)
        graph_kwargs.update(flags)
        graph_kwargs["runtime_profile"] = profile
        return AgentGraph(**graph_kwargs)
