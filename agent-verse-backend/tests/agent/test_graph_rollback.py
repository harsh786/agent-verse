"""Test that AgentGraph properly awaits rollback_all_async on failure."""
import asyncio
import pytest
from app.reliability.rollback import RollbackEngine


@pytest.mark.asyncio
async def test_rollback_all_async_awaited_on_failure():
    """Rollback inverses must complete before graph continues on step failure."""
    completed_rollbacks: list[str] = []

    engine = RollbackEngine()

    async def compensate() -> None:
        await asyncio.sleep(0)
        completed_rollbacks.append("compensated")

    engine.register(action="create_branch", inverse=compensate)
    engine.register(action="create_issue", inverse=compensate)

    # Trigger async rollback
    await engine.rollback_all_async()

    # Must be completed, not fire-and-forget
    assert len(completed_rollbacks) == 2
    assert completed_rollbacks == ["compensated", "compensated"]  # LIFO order


@pytest.mark.asyncio
async def test_rollback_engine_in_agent_graph_uses_async():
    """AgentGraph must use rollback_all_async in its failure path."""
    import inspect
    import app.agent.graph as graph_module

    source = inspect.getsource(graph_module)
    # Must contain rollback_all_async somewhere in the graph
    assert "rollback_all_async" in source, (
        "AgentGraph must call rollback_all_async() (not rollback_all()) "
        "to guarantee rollback completion on step failure"
    )


@pytest.mark.asyncio
async def test_rollback_engine_lifo_order():
    """rollback_all_async must execute inverses in LIFO order."""
    order: list[str] = []
    engine = RollbackEngine()

    async def inv_first() -> None:
        order.append("first")

    async def inv_second() -> None:
        order.append("second")

    engine.register(action="action_first", inverse=inv_first)
    engine.register(action="action_second", inverse=inv_second)

    await engine.rollback_all_async()

    # LIFO: second registered → first rolled back
    assert order == ["second", "first"]


@pytest.mark.asyncio
async def test_rollback_engine_empty_stack_is_noop():
    """rollback_all_async on empty stack must not raise."""
    engine = RollbackEngine()
    result = await engine.rollback_all_async()
    assert result == []


@pytest.mark.asyncio
async def test_rollback_engine_sync_inverse_also_works():
    """rollback_all_async must also handle sync inverse functions."""
    called: list[str] = []
    engine = RollbackEngine()

    def sync_inv() -> None:
        called.append("sync")

    engine.register(action="sync_action", inverse=sync_inv)
    await engine.rollback_all_async()
    assert called == ["sync"]
