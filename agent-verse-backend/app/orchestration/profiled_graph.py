"""Build a goal's execution kernel from its runtime profile — one implementation.

The in-process path (``GoalService._make_agent_loop_for_tenant``) and the Celery
worker (``app.scaling.tasks.run_goal``) both call :func:`build_profiled_graph`,
so a queued goal runs the same strategy an in-process goal would. The worker
used to build a plain AgentGraph whatever the persisted profile said, while the
goal was recorded as running the profile's strategy.

What actually runs is reported in the returned ``strategy_execution`` record —
never inferred from what the profile asked for — so a downgrade (no
StrategyRunner for a DISTRIBUTED strategy, a strategy with no AgentGraph node)
is visible instead of silent.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)


def build_profiled_graph(
    runtime_profile: Any | None,
    graph_services: dict[str, Any],
    agent_config: dict[str, Any] | None = None,
    *,
    distributed_loop_builder: Callable[[], Any | None] | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Return ``(graph, strategy_execution)`` for ``runtime_profile``.

    ``runtime_profile`` is ``None`` for a goal the strategy-runtime rollout does
    not let the profile drive: the agent-config graph runs (``graph_services``
    carries its pattern flags). ``distributed_loop_builder`` returns a
    DistributedStrategyLoop when this process has a real StrategyRunner, else
    ``None`` (the worker passes none): the profile is then compiled on the local
    tier when the strategy has an AgentGraph node, else a plain ReAct loop runs —
    each recorded as a downgrade.
    """
    from app.agent.graph import AgentGraph
    from app.orchestration.execution_drivers import describe_agent_graph_execution
    from app.orchestration.graph_factory import GraphFactory
    from app.orchestration.strategy_adapters import ExecutionTier

    strategy_execution: dict[str, Any] = {"driver": "agent_graph"}
    downgrades: list[dict[str, str]] = []
    graph: Any
    if runtime_profile is not None:
        requested = runtime_profile.primary_strategy.strategy_id
        strategy_execution["requested_primary"] = requested
        strategy_execution["profile_id"] = runtime_profile.profile_id
        if runtime_profile.execution_tier is ExecutionTier.DISTRIBUTED:
            distributed = distributed_loop_builder() if distributed_loop_builder else None
            if distributed is not None:
                graph = distributed
                strategy_execution["driver"] = "strategy_runner"
                strategy_execution["patterns"] = [requested]
            else:
                _log.warning(
                    "distributed_strategy_runner_unavailable_local_fallback",
                    strategy_id=requested,
                    goal_id=runtime_profile.goal_id,
                )
                # supervisor / debate / goal_tree also exist as local AgentGraph
                # nodes: compile the same profile on the local tier so the requested
                # pattern still runs, rather than a bare ReAct loop claiming it.
                try:
                    graph = GraphFactory().create(
                        dataclasses.replace(runtime_profile, execution_tier=ExecutionTier.LOCAL),
                        graph_services,
                        agent_config=agent_config,
                    )
                    downgrades.append(
                        {
                            "strategy_id": requested,
                            "from": "strategy_runner",
                            "to": "agent_graph",
                            "reason": "strategy_runner_unavailable",
                        }
                    )
                except ValueError:
                    graph = AgentGraph(**graph_services)
                    downgrades.append(
                        {
                            "strategy_id": requested,
                            "from": "strategy_runner",
                            "to": "react",
                            "reason": "strategy_runner_unavailable_no_local_node",
                        }
                    )
        else:
            try:
                graph = GraphFactory().create(
                    runtime_profile, graph_services, agent_config=agent_config
                )
            except ValueError as exc:
                _log.error(
                    "graph_factory_compile_failed_local_fallback",
                    error=str(exc),
                    goal_id=runtime_profile.goal_id,
                )
                graph = AgentGraph(**graph_services)
                downgrades.append(
                    {
                        "strategy_id": requested,
                        "from": "agent_graph",
                        "to": "react",
                        "reason": "graph_compile_failed",
                    }
                )
    else:
        graph = AgentGraph(**graph_services)
    if "patterns" not in strategy_execution:
        strategy_execution["patterns"] = describe_agent_graph_execution(graph)
    if downgrades:
        strategy_execution["downgrades"] = downgrades
    return graph, strategy_execution
