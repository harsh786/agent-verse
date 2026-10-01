"""Which strategies a goal can actually execute, and by which driver.

The registry catalogues ~100 strategies and many carry a real adapter, but a goal only
ever runs on one of two drivers:

* the local :class:`~app.agent.graph.AgentGraph` kernel — which implements a fixed set of
  reasoning / multi-agent nodes, each switched on by a constructor flag; and
* the app-wired :class:`~app.orchestration.strategy_runner.StrategyRunner` — whose
  executor has a genuine driver for only a few DISTRIBUTED strategies.

Everything else (ReWOO, LATS, CodeAct, magentic, …) has adapter logic but nothing that turns
a goal string into a run of it. Before this module the runtime profile could name such a
strategy while the goal silently ran a plain AgentGraph. This is the single source of truth
the profile builder, GraphFactory, pattern selector and strategy catalogue consult, so the
profile only ever names a pattern that actually runs (and records the honest downgrade when
it cannot).
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from app.orchestration.strategy_adapters import ExecutionTier
from app.orchestration.strategy_registry import (
    StrategyCapability,
    StrategyRegistry,
    StrategyState,
)

# Strategy id -> the AgentGraph constructor flag that compiles it in (``None``: the base
# plan -> execute -> verify loop *is* the strategy, so no flag is needed).
AGENT_GRAPH_STRATEGY_FLAGS: Mapping[str, str | None] = MappingProxyType(
    {
        "react": None,
        "plan_execute": None,
        # The planner recalls reflexion lessons whenever the reflexion service is wired.
        "reflexion": None,
        # The structured executor honours step.loop_until and runs dependency waves.
        "loop_until": None,
        "wave_execution": None,
        "chain_of_thought": "enable_cot",
        "reflection": "enable_reflection",
        "self_refine": "enable_self_refine",
        "self_consistency": "enable_self_consistency",
        "tree_of_thoughts": "enable_tree_of_thoughts",
        "peer_review": "enable_peer_review",
        "supervisor": "enable_supervisor",
        "debate": "enable_debate",
        "goal_tree": "enable_goal_tree",
    }
)

# DISTRIBUTED strategies whose StrategyRunner executor has a genuine goal driver
# (see app/orchestration/strategy_executor.py). Every other DISTRIBUTED strategy is denied
# at admission with ``strategy_execution_not_implemented``.
# The coordination patterns run through the coordination pattern runtime
# (app.coordination.pattern_runs) on a coordination session created for the goal.
COORDINATION_PATTERN_STRATEGIES: frozenset[str] = frozenset(
    {
        "magentic",
        "mixture_of_agents",
        "camel",
        "generative_agents",
        "decentralized_swarm",
        "market_auction",
    }
)
STRATEGY_RUNNER_STRATEGIES: frozenset[str] = (
    frozenset({"supervisor", "goal_tree", "debate"}) | COORDINATION_PATTERN_STRATEGIES
)

# Sentinel the selector emits for "no multi-agent coordination" — not a strategy.
SINGLE_AGENT = "single_agent"

NO_EXECUTION_DRIVER = "no_execution_driver"


class ExecutionDriver(enum.StrEnum):
    AGENT_GRAPH = "agent_graph"
    STRATEGY_RUNNER = "strategy_runner"


def goal_execution_driver(capability: StrategyCapability) -> ExecutionDriver | None:
    """The driver that runs *capability* as a goal's strategy, or ``None`` if nothing can."""
    if capability.state in (StrategyState.PLANNED, StrategyState.DISABLED):
        return None
    if capability.adapter_descriptor is None:
        return None
    strategy_id = capability.strategy_id
    tier = capability.execution_tier
    if tier is ExecutionTier.DISTRIBUTED and strategy_id in STRATEGY_RUNNER_STRATEGIES:
        return ExecutionDriver.STRATEGY_RUNNER
    if tier is ExecutionTier.LOCAL and strategy_id in AGENT_GRAPH_STRATEGY_FLAGS:
        return ExecutionDriver.AGENT_GRAPH
    return None


@dataclass(frozen=True, slots=True)
class StrategyAvailability:
    """What the catalogue may truthfully say about running a strategy."""

    availability: str  # available | experimental | cross_cutting | not_available
    execution_driver: str | None
    reason: str | None


def strategy_availability(capability: StrategyCapability) -> StrategyAvailability:
    """Catalogue truth: registered adapter logic is not the same as runnable.

    * ``available`` — a driver runs it (AgentGraph, the StrategyRunner, or — for RAG
      strategies — the RAG runtime adapter);
    * ``experimental`` — adapter logic exists but nothing turns a goal into a run of it
      (e.g. DISTRIBUTED strategies the StrategyRunner denies at admission);
    * ``cross_cutting`` — an always-on platform capability, not a selectable strategy;
    * ``not_available`` — planned or disabled.
    """
    if capability.state in (StrategyState.PLANNED, StrategyState.DISABLED):
        return StrategyAvailability("not_available", None, capability.state.value)
    driver = goal_execution_driver(capability)
    if driver is not None:
        return StrategyAvailability("available", driver.value, None)
    tier = capability.execution_tier
    if tier is ExecutionTier.RAG and capability.runtime_adapter is not None:
        return StrategyAvailability("available", "rag_runtime", None)
    if tier is ExecutionTier.CROSS_CUTTING:
        return StrategyAvailability("cross_cutting", None, "platform_capability")
    if tier is ExecutionTier.DISTRIBUTED:
        return StrategyAvailability("experimental", None, "strategy_execution_not_implemented")
    return StrategyAvailability("experimental", None, NO_EXECUTION_DRIVER)


def is_goal_executable(registry: StrategyRegistry, strategy_id: str) -> bool:
    try:
        capability = registry.resolve(strategy_id).capability
    except LookupError:
        return False
    return goal_execution_driver(capability) is not None


def has_agent_graph_node(strategy_id: str) -> bool:
    """True when the local AgentGraph kernel can compile *strategy_id* in."""
    return strategy_id in AGENT_GRAPH_STRATEGY_FLAGS


def describe_agent_graph_execution(graph: Any) -> list[str]:
    """The strategies a compiled AgentGraph will actually run, read off its flags.

    Ground truth for "which pattern ran": derived from the constructed graph, not from
    what the profile or the agent config asked for.
    """
    profile = getattr(graph, "runtime_profile", None)
    primary = getattr(getattr(profile, "primary_strategy", None), "strategy_id", None)
    base = "react"
    if isinstance(primary, str) and primary in AGENT_GRAPH_STRATEGY_FLAGS:
        # A flag-less strategy is the base loop itself (react / plan_execute / …).
        base = primary if AGENT_GRAPH_STRATEGY_FLAGS[primary] is None else base
    executed: list[str] = [base]
    for strategy_id, flag in AGENT_GRAPH_STRATEGY_FLAGS.items():
        if flag is None:
            continue
        if bool(getattr(graph, f"_{flag}", False)):
            executed.append(strategy_id)
    return list(dict.fromkeys(executed))


__all__ = [
    "AGENT_GRAPH_STRATEGY_FLAGS",
    "NO_EXECUTION_DRIVER",
    "SINGLE_AGENT",
    "STRATEGY_RUNNER_STRATEGIES",
    "ExecutionDriver",
    "StrategyAvailability",
    "describe_agent_graph_execution",
    "goal_execution_driver",
    "has_agent_graph_node",
    "is_goal_executable",
    "strategy_availability",
]
