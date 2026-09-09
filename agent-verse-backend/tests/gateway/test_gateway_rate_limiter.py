"""Phase 2 — Gateway rate limiter tests.

Covers app/gateway/rate_limiter.py:
  - ChannelRateLimiter in-memory fallback (no Redis required)
  - RateLimitExceeded exception structure
  - CHANNEL_LIMITS / ACTION_LIMITS constants
  - check_and_increment allow / deny paths
"""
from __future__ import annotations

import pytest

from app.gateway.rate_limiter import (
    ACTION_LIMITS,
    CHANNEL_LIMITS,
    ChannelRateLimiter,
    RateLimitExceeded,
)

# ── RateLimitExceeded ─────────────────────────────────────────────────────────

class TestRateLimitExceededException:
    def test_attributes_set(self) -> None:
        exc = RateLimitExceeded(limit=100, window_seconds=3600, retry_after=60)
        assert exc.limit == 100
        assert exc.window_seconds == 3600
        assert exc.retry_after == 60

    def test_str_contains_limit(self) -> None:
        exc = RateLimitExceeded(limit=10, window_seconds=60, retry_after=30)
        assert "10" in str(exc)

    def test_is_exception_subclass(self) -> None:
        assert issubclass(RateLimitExceeded, Exception)


# ── CHANNEL_LIMITS constants ──────────────────────────────────────────────────

class TestChannelLimitsConstants:
    def test_all_channels_have_limit_and_window(self) -> None:
        for channel, cfg in CHANNEL_LIMITS.items():
            assert "limit" in cfg, f"{channel} missing 'limit'"
            assert "window" in cfg, f"{channel} missing 'window'"
            assert cfg["limit"] > 0
            assert cfg["window"] > 0

    def test_mcp_channel_has_higher_limit_than_email(self) -> None:
        """MCP is machine-to-machine and should have higher throughput."""
        assert CHANNEL_LIMITS["mcp"]["limit"] > CHANNEL_LIMITS["email"]["limit"]

    def test_a2a_channel_highest_limit(self) -> None:
        """A2A (agent-to-agent) should have the highest limit."""
        assert CHANNEL_LIMITS["a2a"]["limit"] >= max(
            cfg["limit"] for ch, cfg in CHANNEL_LIMITS.items() if ch != "a2a"
        )

    def test_voice_channels_per_minute_window(self) -> None:
        """Voice endpoints need per-minute granularity, not per-hour."""
        assert CHANNEL_LIMITS["voice_transcribe"]["window"] <= 60
        assert CHANNEL_LIMITS["voice_speak"]["window"] <= 60

    def test_action_limits_exist_for_mission_and_approve(self) -> None:
        assert "start_mission" in ACTION_LIMITS
        assert "approve" in ACTION_LIMITS


# ── ChannelRateLimiter in-memory fallback ────────────────────────────────────

class TestChannelRateLimiterMemory:
    """Tests run entirely in-memory (no Redis) to avoid infrastructure deps."""

    @pytest.fixture
    def limiter(self) -> ChannelRateLimiter:
        return ChannelRateLimiter(redis_client=None)

    @pytest.mark.asyncio
    async def test_first_call_allowed(self, limiter: ChannelRateLimiter) -> None:
        result = await limiter.check_and_increment(
            tenant_id="t1", org_id="org1", channel="rest", actor_id="user1"
        )
        assert result["allowed"] is True

    @pytest.mark.asyncio
    async def test_remaining_decrements(self, limiter: ChannelRateLimiter) -> None:
        r1 = await limiter.check_and_increment(
            tenant_id="t1", org_id="org1", channel="rest", actor_id="user1"
        )
        r2 = await limiter.check_and_increment(
            tenant_id="t1", org_id="org1", channel="rest", actor_id="user1"
        )
        assert r2["remaining"] <= r1["remaining"]

    @pytest.mark.asyncio
    async def test_different_tenants_independent(self, limiter: ChannelRateLimiter) -> None:
        # Tenant A should not consume Tenant B's quota
        for _ in range(5):
            await limiter.check_and_increment(
                tenant_id="tenant-A", org_id="org1", channel="email", actor_id="u"
            )
        # Tenant B should still have full quota
        result = await limiter.check_and_increment(
            tenant_id="tenant-B", org_id="org1", channel="email", actor_id="u"
        )
        assert result["allowed"] is True

    @pytest.mark.asyncio
    async def test_different_channels_independent(self, limiter: ChannelRateLimiter) -> None:
        for _ in range(3):
            await limiter.check_and_increment(
                tenant_id="t1", org_id="org1", channel="telegram", actor_id="u"
            )
        result = await limiter.check_and_increment(
            tenant_id="t1", org_id="org1", channel="slack", actor_id="u"
        )
        assert result["allowed"] is True

    @pytest.mark.asyncio
    async def test_limit_exceeded_raises_rate_limit_exceeded(
        self, limiter: ChannelRateLimiter
    ) -> None:
        """Exhaust the email limit (10/hour) — next call raises RateLimitExceeded."""
        email_limit = CHANNEL_LIMITS["email"]["limit"]
        for _ in range(email_limit):
            await limiter.check_and_increment(
                tenant_id="exhaust-t", org_id="org1", channel="email", actor_id="u"
            )
        with pytest.raises(RateLimitExceeded) as exc_info:
            await limiter.check_and_increment(
                tenant_id="exhaust-t", org_id="org1", channel="email", actor_id="u"
            )
        assert exc_info.value.limit == email_limit

    @pytest.mark.asyncio
    async def test_unknown_channel_falls_back_to_rest_limit(
        self, limiter: ChannelRateLimiter
    ) -> None:
        """Unknown channels fall back to 'rest' limits — should not crash."""
        result = await limiter.check_and_increment(
            tenant_id="t1", org_id="org1", channel="nonexistent_channel", actor_id="u"
        )
        assert isinstance(result, dict)
        assert result["allowed"] is True

    @pytest.mark.asyncio
    async def test_result_has_required_keys(self, limiter: ChannelRateLimiter) -> None:
        result = await limiter.check_and_increment(
            tenant_id="t1", org_id="org1", channel="rest", actor_id="actor-keys"
        )
        assert "allowed" in result
        assert "remaining" in result
        assert "reset_at" in result or "window" in result
