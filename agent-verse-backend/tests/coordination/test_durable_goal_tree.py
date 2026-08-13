from app.coordination.patterns.goal_tree_adapter import (
    DurableGoalTreeAdapter,
    DurableGoalTreeRuntime,
)
from app.orchestration.strategy_adapters import ExecutionTier


def test_goal_tree_uses_distributed_canonical_wave_runtime() -> None:
    adapter = DurableGoalTreeAdapter()
    assert adapter.strategy_id == "goal_tree"
    assert adapter.execution_tier is ExecutionTier.DISTRIBUTED
    assert isinstance(adapter.create_runtime(checkpoint_store=object()), DurableGoalTreeRuntime)
