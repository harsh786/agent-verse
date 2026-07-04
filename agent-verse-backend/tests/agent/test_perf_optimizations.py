"""Tests for all remaining performance optimizations.

Covers:
- GoalDeduplicator (app/services/dedup.py)
- PromptCompressor (app/agent/prompt_compressor.py)
- ModelRouter complexity tiering + model_for_goal (app/agent/model_router.py)
- CostController.get_cost_tier (app/governance/cost.py)
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ═══════════════════════════════════════════════════════════════════════════════
# GoalDeduplicator
# ═══════════════════════════════════════════════════════════════════════════════

class TestGoalDeduplicator:

    def _make(self, redis=None):
        from app.services.dedup import GoalDeduplicator
        return GoalDeduplicator(redis=redis, ttl=60)

    @pytest.mark.asyncio
    async def test_no_existing_returns_none(self):
        d = self._make()
        result = await d.get_existing("tenant1", "find open tickets")
        assert result is None

    @pytest.mark.asyncio
    async def test_register_then_get_returns_id(self):
        d = self._make()
        await d.register("tenant1", "find open tickets", "goal-abc123")
        result = await d.get_existing("tenant1", "find open tickets")
        assert result == "goal-abc123"

    @pytest.mark.asyncio
    async def test_different_tenant_no_dedup(self):
        d = self._make()
        await d.register("tenant1", "find open tickets", "goal-1")
        result = await d.get_existing("tenant2", "find open tickets")
        assert result is None

    @pytest.mark.asyncio
    async def test_different_goal_no_dedup(self):
        d = self._make()
        await d.register("tenant1", "find open tickets", "goal-1")
        result = await d.get_existing("tenant1", "list all projects")
        assert result is None

    @pytest.mark.asyncio
    async def test_goal_case_insensitive(self):
        d = self._make()
        await d.register("tenant1", "Find Open Tickets", "goal-1")
        result = await d.get_existing("tenant1", "find open tickets")
        assert result == "goal-1"

    @pytest.mark.asyncio
    async def test_goal_whitespace_normalised(self):
        d = self._make()
        await d.register("tenant1", "  find open tickets  ", "goal-1")
        result = await d.get_existing("tenant1", "find open tickets")
        assert result == "goal-1"

    @pytest.mark.asyncio
    async def test_release_clears_dedup(self):
        d = self._make()
        await d.register("tenant1", "find open tickets", "goal-1")
        await d.release("tenant1", "find open tickets")
        result = await d.get_existing("tenant1", "find open tickets")
        assert result is None

    @pytest.mark.asyncio
    async def test_redis_set_nx_used(self):
        redis = AsyncMock()
        redis.set.return_value = True
        redis.get.return_value = None
        d = self._make(redis=redis)
        registered = await d.register("t", "goal text", "g1")
        assert registered is True
        # Verify NX flag was used
        redis.set.assert_called_once()
        call_kwargs = redis.set.call_args[1]
        assert call_kwargs.get("nx") is True

    @pytest.mark.asyncio
    async def test_redis_set_nx_returns_false_on_race(self):
        redis = AsyncMock()
        redis.set.return_value = None  # NX failed — key already set
        d = self._make(redis=redis)
        registered = await d.register("t", "goal text", "g2")
        assert registered is False

    @pytest.mark.asyncio
    async def test_redis_error_falls_back_to_mem(self):
        redis = AsyncMock()
        redis.get.side_effect = ConnectionError("redis down")
        redis.set.side_effect = ConnectionError("redis down")
        d = self._make(redis=redis)
        # Should not raise
        result = await d.get_existing("t", "goal")
        assert result is None

    @pytest.mark.asyncio
    async def test_redis_get_existing(self):
        redis = AsyncMock()
        redis.get.return_value = b"goal-xyz"
        d = self._make(redis=redis)
        result = await d.get_existing("t", "goal text")
        assert result == "goal-xyz"

    @pytest.mark.asyncio
    async def test_redis_release_deletes_key(self):
        redis = AsyncMock()
        d = self._make(redis=redis)
        await d.release("t", "goal text")
        redis.delete.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# PromptCompressor
# ═══════════════════════════════════════════════════════════════════════════════

class TestPromptCompressor:

    def _make(self):
        from app.agent.prompt_compressor import PromptCompressor
        return PromptCompressor()

    def test_empty_string_unchanged(self):
        c = self._make()
        assert c.compress("") == ""

    def test_collapses_triple_blank_lines(self):
        c = self._make()
        text = "line1\n\n\n\nline2"
        result = c.compress(text)
        assert "\n\n\n" not in result
        assert "line1" in result
        assert "line2" in result

    def test_strips_trailing_whitespace(self):
        c = self._make()
        result = c.compress("hello   \nworld  ")
        assert "   " not in result

    def test_removes_filler_phrase(self):
        c = self._make()
        text = "As an AI language model, you should do X."
        result = c.compress(text)
        assert "As an AI language model," not in result

    def test_truncates_long_rag_block(self):
        c = self._make().__class__(max_rag_chars=50)
        long_context = "[Relevant context]\n" + "A" * 200
        result = c.compress(long_context)
        assert "truncated" in result
        assert len(result) < 200

    def test_does_not_truncate_short_rag(self):
        c = self._make()
        text = "[Relevant context]\nshort context here"
        result = c.compress(text)
        assert "short context here" in result
        assert "truncated" not in result

    def test_caps_long_tool_list(self):
        from app.agent.prompt_compressor import _MAX_TOOL_LIST_ITEMS
        c = self._make()
        tools = "\n".join(f"  - tool_{i}: description" for i in range(_MAX_TOOL_LIST_ITEMS + 10))
        text = "[Available tools]\n" + tools
        result = c.compress(text)
        assert "omitted" in result

    def test_short_tool_list_unchanged(self):
        c = self._make()
        text = "[Available tools]\n  - search_issues: find tickets\n  - list_projects: list"
        result = c.compress(text)
        assert "search_issues" in result
        assert "list_projects" in result
        assert "omitted" not in result

    def test_compress_messages(self):
        c = self._make()
        messages = [
            {"role": "system", "content": "sys\n\n\n\nprompt"},
            {"role": "user", "content": "user msg\n\n\n\nwith gaps"},
        ]
        result = c.compress_messages(messages)
        for m in result:
            assert "\n\n\n" not in m["content"]

    def test_stats_updated(self):
        c = self._make()
        c.compress("long text\n\n\n\nwith gaps   ")
        s = c.stats()
        assert s["calls"] >= 1

    def test_non_string_content_preserved(self):
        c = self._make()
        messages = [{"role": "user", "content": 42}]  # non-string content
        result = c.compress_messages(messages)
        assert result[0]["content"] == 42


# ═══════════════════════════════════════════════════════════════════════════════
# ModelRouter: complexity tiering + model_for_goal
# ═══════════════════════════════════════════════════════════════════════════════

class TestModelRouterComplexityTiering:

    def _make(self, provider="anthropic"):
        from app.agent.model_router import ModelRouter
        return ModelRouter(provider_name=provider)

    def test_simple_goal_detected(self):
        router = self._make()
        assert router.complexity_tier("list all open tickets") == "simple"
        assert router.complexity_tier("get the ticket status") == "simple"
        assert router.complexity_tier("show me the projects") == "simple"
        assert router.complexity_tier("what is the sprint velocity") == "simple"

    def test_complex_goal_detected(self):
        router = self._make()
        assert router.complexity_tier("create a CI/CD pipeline") == "complex"
        assert router.complexity_tier("deploy the application to production") == "complex"
        assert router.complexity_tier("analyze and refactor the auth module") == "complex"

    def test_medium_goal_default(self):
        router = self._make()
        assert router.complexity_tier("summarize the last 5 commits") == "medium"

    def test_model_for_goal_simple_downgrades_planning(self):
        router = self._make("anthropic")
        planning_model = router.model_for("planning")
        downgraded = router.model_for_goal("planning", goal="list all open tickets")
        # Simple goals should use execution model (cheaper)
        execution_model = router.model_for("execution")
        assert downgraded == execution_model
        assert downgraded != planning_model

    def test_model_for_goal_complex_keeps_planning_model(self):
        router = self._make("anthropic")
        planning_model = router.model_for("planning")
        result = router.model_for_goal("planning", goal="deploy application to kubernetes")
        assert result == planning_model

    def test_model_for_goal_verification_never_downgraded(self):
        router = self._make("anthropic")
        verify_model = router.model_for("verification")
        # Even simple goals should not affect verification model
        result = router.model_for_goal("verification", goal="list all open tickets")
        assert result == verify_model

    def test_model_for_goal_no_goal_uses_base(self):
        router = self._make("anthropic")
        planning_model = router.model_for("planning")
        result = router.model_for_goal("planning", goal="")
        assert result == planning_model

    def test_openai_provider_downgrades_correctly(self):
        router = self._make("openai")
        downgraded = router.model_for_goal("planning", goal="show me the open tickets")
        execution_model = router.model_for("execution")
        assert downgraded == execution_model


# ═══════════════════════════════════════════════════════════════════════════════
# CostController.get_cost_tier
# ═══════════════════════════════════════════════════════════════════════════════

class TestCostTierDowngrade:

    def _make_tenant_ctx(self, tenant_id="test-tenant"):
        ctx = MagicMock()
        ctx.tenant_id = tenant_id
        return ctx

    @pytest.mark.asyncio
    async def test_premium_tier_below_60_pct(self):
        from app.governance.cost import RedisCostController
        controller = RedisCostController.__new__(RedisCostController)
        controller._redis = None
        controller._goal_totals = {}
        controller._daily_totals = {}

        # get_budget_status(tenant_id, goal_id=...) — positional first arg
        async def _mock_status(tenant_id, goal_id=None):
            return {"budget_pct_remaining": 0.50}  # 50% remaining → 50% used

        controller.get_budget_status = _mock_status
        tier = await controller.get_cost_tier(
            goal_id="g1", tenant_ctx=self._make_tenant_ctx()
        )
        assert tier == "premium"

    @pytest.mark.asyncio
    async def test_standard_tier_between_60_85_pct(self):
        from app.governance.cost import RedisCostController
        controller = RedisCostController.__new__(RedisCostController)

        # get_budget_status(tenant_id, goal_id=...) — positional first arg
        async def _mock_status(tenant_id, goal_id=None):
            return {"budget_pct_remaining": 0.25}  # 25% remaining → 75% used

        controller.get_budget_status = _mock_status
        tier = await controller.get_cost_tier(
            goal_id="g1", tenant_ctx=self._make_tenant_ctx()
        )
        assert tier == "standard"

    @pytest.mark.asyncio
    async def test_economy_tier_above_85_pct(self):
        from app.governance.cost import RedisCostController
        controller = RedisCostController.__new__(RedisCostController)

        async def _mock_status(tenant_id, goal_id=None):
            return {"budget_pct_remaining": 0.10}  # 10% remaining → 90% used

        controller.get_budget_status = _mock_status
        tier = await controller.get_cost_tier(
            goal_id="g1", tenant_ctx=self._make_tenant_ctx()
        )
        assert tier == "economy"

    @pytest.mark.asyncio
    async def test_error_returns_premium(self):
        from app.governance.cost import RedisCostController
        controller = RedisCostController.__new__(RedisCostController)

        async def _mock_status(tenant_id, goal_id=None):
            raise RuntimeError("redis down")

        controller.get_budget_status = _mock_status
        tier = await controller.get_cost_tier(
            goal_id="g1", tenant_ctx=self._make_tenant_ctx()
        )
        assert tier == "premium"  # safe default — never penalise on error
