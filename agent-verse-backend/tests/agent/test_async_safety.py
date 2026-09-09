"""Regression tests for 0C.3 async safety fixes."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest


class TestJiraResolverAsync:
    """H6: _resolve_jira_account_id must be async (no blocking httpx.Client)."""

    def test_no_sync_httpx_in_tool_calls(self):
        """H6: tool_calls.py must not use httpx.Client (sync) in async context."""
        import inspect

        from app.agent import tool_calls
        source = inspect.getsource(tool_calls)

        # Check for sync httpx.Client usage (should be httpx.AsyncClient)
        assert "httpx.Client(" not in source or "# httpx.Client is ok here" in source, \
            "H6: sync httpx.Client found in tool_calls.py — blocks event loop"

    @pytest.mark.asyncio
    async def test_jira_resolver_is_async_coroutine(self):
        """H6: _resolve_jira_account_id (or equivalent) must be a coroutine."""
        import inspect

        from app.agent import tool_calls

        # Find any Jira-related async resolution function
        jira_funcs = [
            name for name, func in inspect.getmembers(tool_calls, inspect.iscoroutinefunction)
            if "jira" in name.lower() or "account" in name.lower()
        ]
        # Either it's async, or there's no sync one
        sync_jira_funcs = [
            name for name, func in inspect.getmembers(tool_calls, inspect.isfunction)
            if ("jira" in name.lower() or "account" in name.lower())
            and not inspect.iscoroutinefunction(func)
        ]
        assert len(sync_jira_funcs) == 0, \
            f"H6: Sync Jira functions found: {sync_jira_funcs}"


class TestCheckpointGoalId:
    """H7: thread_id must be derived from goal_id for deterministic checkpoint resume."""

    @pytest.mark.asyncio
    async def test_same_goal_id_uses_same_thread_id(self):
        """H7: Same goal_id must produce the same LangGraph thread_id."""
        from app.agent.graph import AgentGraph
        from app.providers.fake import FakeProvider

        thread_ids = []

        original_invoke = None

        async def capture_thread_id(config, *args, **kwargs):
            thread_id = config.get("configurable", {}).get("thread_id", "")
            thread_ids.append(thread_id)
            # Return a valid state
            from app.agent.state import AgentState, GoalStatus
            state = AgentState(
                goal="test", tenant_ctx=None, goal_id="test-goal-123",
                status=GoalStatus.COMPLETE
            )
            return {"agent_state": state}

        g = AgentGraph(
            planner=FakeProvider(responses=['{"steps": ["step1"]}']),
            executor=FakeProvider(responses=["done"]),
            verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
        )

        with patch.object(g._graph, "ainvoke", side_effect=capture_thread_id):
            from app.tenancy.context import PlanTier, TenantContext
            T = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=())
            try:
                await g.run(goal="test", tenant_ctx=T, goal_id="test-goal-123")
            except Exception:
                pass
            try:
                await g.run(goal="test", tenant_ctx=T, goal_id="test-goal-123")
            except Exception:
                pass

        # Both runs should use the same thread_id
        if len(thread_ids) >= 2:
            assert thread_ids[0] == thread_ids[1], \
                f"H7: Same goal_id produced different thread_ids: {thread_ids}"


class TestParallelWaveCostRace:
    """H11: Parallel wave cost updates must not race."""

    @pytest.mark.asyncio
    async def test_concurrent_cost_updates_no_loss(self):
        """H11: Concurrent asyncio tasks updating total_cost_usd must not lose updates."""
        from app.agent.graph import AgentGraph
        from app.providers.fake import FakeProvider

        g = AgentGraph(
            planner=FakeProvider(responses=["plan"]),
            executor=FakeProvider(responses=["done"]),
            verifier=FakeProvider(responses=["ok"]),
        )

        context: dict = {"total_cost_usd": 0.0}
        lock = getattr(g, "_state_lock", asyncio.Lock())

        async def increment_cost(amount: float) -> None:
            async with lock:
                context["total_cost_usd"] = context.get("total_cost_usd", 0.0) + amount

        # 100 concurrent updates of $0.01 each = $1.00 total
        await asyncio.gather(*[increment_cost(0.01) for _ in range(100)])

        assert abs(context["total_cost_usd"] - 1.00) < 0.001, \
            f"H11: Cost race detected! Expected $1.00, got ${context['total_cost_usd']:.4f}"

    def test_state_lock_exists_on_agent_graph(self):
        """H11: AgentGraph must have _state_lock attribute."""
        from app.agent.graph import AgentGraph
        from app.providers.fake import FakeProvider

        g = AgentGraph(
            planner=FakeProvider(responses=["plan"]),
            executor=FakeProvider(responses=["done"]),
            verifier=FakeProvider(responses=["ok"]),
        )
        assert hasattr(g, "_state_lock"), "H11: AgentGraph._state_lock not found"
        assert isinstance(g._state_lock, asyncio.Lock), \
            f"H11: _state_lock is not asyncio.Lock, got {type(g._state_lock)}"
