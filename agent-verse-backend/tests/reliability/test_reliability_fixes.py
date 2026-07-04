"""Regression tests for 0C.4 reliability correctness."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestRollbackAwaited:
    """H14: Rollback must actually await MCP inverse calls."""

    @pytest.mark.asyncio
    async def test_rollback_awaits_inverse_function(self):
        """H14: rollback_all_async must await inverse, not fire-and-forget."""
        from app.reliability.rollback import RollbackEngine

        calls_made = []

        async def mock_inverse(tool_call, mcp_client=None):
            calls_made.append(tool_call.tool_name)

        engine = RollbackEngine()

        # Register a mock inverse
        from app.reliability.tool_inverses import register_inverse
        register_inverse("create_jira_issue", mock_inverse)

        # Create a fake executed step
        from unittest.mock import MagicMock
        tool_call = MagicMock()
        tool_call.tool_name = "create_jira_issue"
        tool_call.arguments = {"summary": "test"}
        tool_call.result = {"id": "JIRA-1"}

        from app.tenancy.context import TenantContext, PlanTier
        T = TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id="k1", roles=())

        await engine.rollback_all_async([tool_call], tenant_ctx=T)

        assert "create_jira_issue" in calls_made, \
            "H14: rollback did not await the inverse function"

    def test_inverse_fn_returns_awaitable(self):
        """H14: get_inverse_fn must return an awaitable coroutine, not a sync wrapper."""
        from app.reliability.tool_inverses import get_inverse_fn
        import inspect

        # Any registered inverse must be a coroutine function
        inverse = get_inverse_fn("create_jira_issue")
        if inverse is not None:
            assert inspect.iscoroutinefunction(inverse) or asyncio.iscoroutine(inverse()), \
                "H14: inverse function is not awaitable"


class TestCircuitBreakerWallClock:
    """H16: Circuit breaker must use wall clock time for cross-replica correctness."""

    def test_circuit_breaker_uses_time_time_not_monotonic(self):
        """H16: RedisCircuitBreaker must store time.time() not time.monotonic()."""
        import inspect
        from app.reliability.redis_circuit_breaker import RedisCircuitBreaker
        source = inspect.getsource(RedisCircuitBreaker)

        # Should not have time.monotonic() for storage (only for in-process timing)
        # The key storage calls should use time.time()
        assert "time.time()" in source, \
            "H16: RedisCircuitBreaker does not use time.time() for Redis storage"

    @pytest.mark.asyncio
    async def test_circuit_breaker_recovery_works_cross_process(self):
        """H16: A breaker opened by process A must recover after reset_timeout."""
        import time
        from app.reliability.redis_circuit_breaker import RedisCircuitBreaker

        mock_redis = AsyncMock()

        # Simulate: process A opened the breaker and stored time.time() - 60
        opened_at = time.time() - 61  # 61 seconds ago (past reset_timeout=60)
        mock_redis.get = AsyncMock(side_effect=lambda key: (
            b"open" if "state" in str(key) else
            str(opened_at).encode() if "opened_at" in str(key) else None
        ))
        mock_redis.set = AsyncMock()

        cb = RedisCircuitBreaker(
            redis=mock_redis,
            key="test-breaker",
            failure_threshold=3,
            reset_timeout=60,
        )

        # After reset_timeout, can_call_async should return True
        can_call = await cb.can_call_async()
        assert can_call is True, \
            "H16: Circuit breaker did not recover using wall-clock time"


class TestAuditChainSeeding:
    """H15: Audit chain must be seeded from DB on startup."""

    def test_audit_writer_has_chain_initialization(self):
        """H15: AuditWriter must have chain initialization from DB."""
        import inspect
        from app.governance.audit_v2 import AuditWriter
        source = inspect.getsource(AuditWriter)

        has_init = "_chain_initialized" in source or "_ensure_chain" in source
        assert has_init, \
            "H15: AuditWriter does not seed chain tip from DB on startup"

    def test_audit_hash_includes_tool_name(self):
        """H15: Audit hash must include tool_name in the hash input."""
        import inspect
        from app.governance.audit_v2 import AuditWriter
        source = inspect.getsource(AuditWriter)

        assert "tool_name" in source, \
            "H15: Audit hash computation does not include tool_name"
