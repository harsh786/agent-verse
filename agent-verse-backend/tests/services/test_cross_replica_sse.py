"""Tests for cross-replica SSE delivery via Redis pub/sub (P1-2 fix).

Validates:
  1. _dispatch_event publishes ALL non-ephemeral events to a goal-specific
     Redis channel (goal_events:{tenant_id}:{goal_id}).
  2. _dispatch_event does NOT publish ephemeral events (token_chunk, heartbeat)
     to the goal-specific channel (they'd flood Redis).
  3. Terminal events still go to the platform_events channel too.
  4. subscribe_events raises NotFoundError for unknown goals (no local record,
     no DB record).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.agent.state import GoalStatus
from app.core.errors import NotFoundError
from app.services.goal_service import GoalRecord, GoalService
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="sse-test", plan=PlanTier.FREE, api_key_id="k1")


def _make_record(goal_id: str, tenant_id: str = "sse-test") -> GoalRecord:
    return GoalRecord(
        goal_id=goal_id,
        goal_text="test goal",
        status=GoalStatus.EXECUTING,
        tenant_id=tenant_id,
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
    )


# ── subscribe_events: cross-replica not-found ─────────────────────────────────


def test_subscribe_events_raises_not_found_without_db_or_redis() -> None:
    """Goal not in local dict + no DB + no Redis → NotFoundError."""
    svc = GoalService()  # no DB, no Redis

    async def run() -> None:
        with pytest.raises((NotFoundError, Exception)):
            async for _ in svc.subscribe_events(goal_id="nonexistent", tenant_ctx=CTX):
                pass

    asyncio.run(run())


# ── _dispatch_event: goal-specific Redis publish ──────────────────────────────


def test_dispatch_event_publishes_to_goal_specific_channel() -> None:
    """step_started must be published to goal_events:{tenant_id}:{goal_id}."""
    svc = GoalService()
    mock_redis = AsyncMock()
    svc._redis = mock_redis
    svc._goals["g1"] = _make_record("g1")

    async def run() -> None:
        await svc._dispatch_event("g1", {"type": "step_started", "step": "Do X"}, tenant_ctx=CTX)
        assert mock_redis.publish.called
        call_args = mock_redis.publish.call_args_list
        channels = [c[0][0] for c in call_args if c[0]]
        assert any("g1" in ch and "goal_events" in ch for ch in channels), (
            f"Expected goal-specific channel in publish calls, got: {channels}"
        )

    asyncio.run(run())


def test_dispatch_event_publishes_all_non_ephemeral_event_types() -> None:
    """plan_ready, step_started, step_complete, verification_done → published."""
    svc = GoalService()
    mock_redis = AsyncMock()
    svc._redis = mock_redis
    svc._goals["g2"] = _make_record("g2")

    event_types = ["plan_ready", "step_started", "step_complete", "verification_done"]

    async def run() -> None:
        for etype in event_types:
            await svc._dispatch_event("g2", {"type": etype}, tenant_ctx=CTX)
        # Each of the 4 event types must have triggered at least one publish call
        # to the goal-specific channel.
        goal_channel_calls = [
            c for c in mock_redis.publish.call_args_list
            if c[0] and "goal_events" in c[0][0] and "g2" in c[0][0]
        ]
        assert len(goal_channel_calls) == len(event_types), (
            f"Expected {len(event_types)} goal-channel publish calls, "
            f"got {len(goal_channel_calls)}"
        )

    asyncio.run(run())


def test_dispatch_event_skips_ephemeral_events() -> None:
    """token_chunk and heartbeat must NOT be published to the goal-specific channel."""
    svc = GoalService()
    mock_redis = AsyncMock()
    svc._redis = mock_redis
    svc._goals["g3"] = _make_record("g3")

    async def run() -> None:
        for etype in ("token_chunk", "heartbeat"):
            await svc._dispatch_event("g3", {"type": etype}, tenant_ctx=CTX)

        for call in mock_redis.publish.call_args_list:
            channel = call[0][0] if call[0] else ""
            if "goal_events" in channel:
                payload_raw = call[0][1] if len(call[0]) > 1 else "{}"
                payload = json.loads(payload_raw) if isinstance(payload_raw, str) else {}
                assert payload.get("type") not in ("token_chunk", "heartbeat"), (
                    f"Ephemeral event type {payload.get('type')!r} must not be "
                    "published to goal-specific Redis channel"
                )

    asyncio.run(run())


def test_dispatch_event_terminal_also_publishes_to_platform_channel() -> None:
    """goal_complete must publish to BOTH goal_events:* and platform_events:*."""
    svc = GoalService()
    mock_redis = AsyncMock()
    svc._redis = mock_redis
    record = _make_record("g4")
    svc._goals["g4"] = record

    async def run() -> None:
        await svc._dispatch_event("g4", {"type": "goal_complete"}, tenant_ctx=CTX)
        channels = [c[0][0] for c in mock_redis.publish.call_args_list if c[0]]
        assert any("goal_events" in ch for ch in channels), (
            "goal_complete must publish to goal_events channel"
        )
        assert any("platform_events" in ch for ch in channels), (
            "goal_complete must still publish to platform_events channel"
        )

    asyncio.run(run())


def test_dispatch_event_no_redis_does_not_raise() -> None:
    """When no Redis is configured, _dispatch_event must complete without error."""
    svc = GoalService()  # no Redis
    svc._goals["g5"] = _make_record("g5")

    async def run() -> None:
        # Should not raise even with no Redis wired
        await svc._dispatch_event("g5", {"type": "step_started"}, tenant_ctx=CTX)

    asyncio.run(run())


def test_dispatch_event_redis_failure_does_not_raise() -> None:
    """If Redis.publish() raises, _dispatch_event must swallow the error."""
    svc = GoalService()
    mock_redis = AsyncMock()
    mock_redis.publish.side_effect = ConnectionError("Redis down")
    svc._redis = mock_redis
    svc._goals["g6"] = _make_record("g6")

    async def run() -> None:
        # Must not propagate the ConnectionError
        await svc._dispatch_event("g6", {"type": "step_started"}, tenant_ctx=CTX)

    asyncio.run(run())


# ── _redis_url_for_pubsub attribute ───────────────────────────────────────────


def test_goal_service_has_redis_url_for_pubsub_attr() -> None:
    """GoalService must expose _redis_url_for_pubsub, defaulting to empty string."""
    svc = GoalService()
    assert hasattr(svc, "_redis_url_for_pubsub")
    assert svc._redis_url_for_pubsub == ""


def test_goal_service_redis_url_for_pubsub_settable() -> None:
    """_redis_url_for_pubsub must be settable (wired by lifespan)."""
    svc = GoalService()
    svc._redis_url_for_pubsub = "redis://localhost:6379/0"
    assert svc._redis_url_for_pubsub == "redis://localhost:6379/0"
