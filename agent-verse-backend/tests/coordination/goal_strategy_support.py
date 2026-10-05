"""Drive a goal through DistributedStrategyLoop -> StrategyRunner -> coordination bridge."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.coordination.pattern_runs.goal_bridge import CoordinationGoalBridge
from app.orchestration.distributed_strategy_loop import (
    DistributedStrategyLoop,
    DistributedStrategyOutcome,
)
from app.orchestration.runtime_profile import StrategySelection, default_pattern_limits
from app.orchestration.strategy_context_store import StrategyGoalContextStore
from app.orchestration.strategy_executor import (
    DistributedStrategyExecutor,
    default_distributed_admission,
)
from app.orchestration.strategy_registry import build_default_registry
from app.orchestration.strategy_runner import StrategyRunner


def goal_profile(strategy_id: str, goal_id: str, **limit_overrides: Any) -> Any:
    limits = default_pattern_limits().model_copy(update=limit_overrides)
    return SimpleNamespace(
        goal_id=goal_id,
        profile_id=f"profile-{goal_id}",
        deadline=None,
        effective_limits=limits,
        primary_strategy=StrategySelection(strategy_id, "1.0.0"),
        policy_snapshot_ref="policy:v1",
        budget_snapshot_ref="budget:v1",
    )


async def run_goal(
    state: Any,
    strategy_id: str,
    *,
    goal: str,
    goal_id: str,
    tenant_ctx: Any,
    provider: Any,
    **limit_overrides: Any,
) -> tuple[DistributedStrategyOutcome, list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []

    async def callback(event: dict[str, Any]) -> None:
        events.append(event)

    context_store = StrategyGoalContextStore()
    runner = StrategyRunner(
        build_default_registry(),
        executor=DistributedStrategyExecutor(
            context_store=context_store,
            pattern_bridge=CoordinationGoalBridge(lambda: state, hitl_timeout_seconds=5),
        ),
        admission=default_distributed_admission,
    )
    loop = DistributedStrategyLoop(
        strategy_runner=runner,
        context_store=context_store,
        profile=goal_profile(strategy_id, goal_id, **limit_overrides),
        provider=provider,
    )
    result = await loop.run(
        goal=goal, tenant_ctx=tenant_ctx, event_callback=callback, goal_id=goal_id
    )
    return result, events
