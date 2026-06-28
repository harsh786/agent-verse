"""Tests for Redis BLPOP-based cross-replica HITL delivery."""
from __future__ import annotations

import json

import pytest
from unittest.mock import AsyncMock

from app.governance.hitl import HITLGateway


class TestCrossReplicaHITLDelivery:
    """_wait_for_result / publish_resolution form the cross-replica HITL pair."""

    @pytest.mark.asyncio
    async def test_blpop_wait_receives_result(self) -> None:
        """_wait_for_result returns the payload dict when BLPOP delivers data."""
        payload = {"action": "approve", "approver": "alice", "note": "Looks good"}
        mock_redis = AsyncMock()
        # redis-py BLPOP returns (key_bytes, value_bytes) or None on timeout
        mock_redis.blpop = AsyncMock(
            return_value=(b"hitl_result:req-123", json.dumps(payload).encode())
        )

        gateway = HITLGateway()
        gateway._redis = mock_redis

        result = await gateway._wait_for_result("req-123", timeout=10.0)

        assert result is not None
        assert result["action"] == "approve"
        assert result["approver"] == "alice"
        mock_redis.blpop.assert_called_once()

    @pytest.mark.asyncio
    async def test_blpop_wait_timeout_returns_none(self) -> None:
        """_wait_for_result returns None when BLPOP repeatedly times out."""
        mock_redis = AsyncMock()
        mock_redis.blpop = AsyncMock(return_value=None)  # simulate timeout

        gateway = HITLGateway()
        gateway._redis = mock_redis

        result = await gateway._wait_for_result("req-456", timeout=0.1)

        assert result is None

    @pytest.mark.asyncio
    async def test_blpop_wait_string_data(self) -> None:
        """_wait_for_result handles string (not bytes) Redis response."""
        payload = {"action": "deny", "approver": "charlie", "note": "Out of policy"}
        mock_redis = AsyncMock()
        mock_redis.blpop = AsyncMock(
            return_value=("hitl_result:req-str", json.dumps(payload))  # strings, not bytes
        )

        gateway = HITLGateway()
        gateway._redis = mock_redis

        result = await gateway._wait_for_result("req-str", timeout=5.0)

        assert result is not None
        assert result["action"] == "deny"

    @pytest.mark.asyncio
    async def test_blpop_returns_none_when_no_redis(self) -> None:
        """_wait_for_result returns None immediately when no Redis client."""
        gateway = HITLGateway()
        gateway._redis = None

        result = await gateway._wait_for_result("req-no-redis", timeout=5.0)

        assert result is None

    @pytest.mark.asyncio
    async def test_publish_resolution_pushes_to_redis_list(self) -> None:
        """publish_resolution RPUSHes payload and sets a 24-hour TTL."""
        mock_redis = AsyncMock()
        mock_redis.rpush = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock(return_value=True)

        gateway = HITLGateway()
        gateway._redis = mock_redis

        await gateway.publish_resolution(
            request_id="req-789",
            action="approve",
            approver="bob",
            note="Approved by risk team",
        )

        mock_redis.rpush.assert_called_once()
        key, payload_str = mock_redis.rpush.call_args[0]
        assert key == "hitl_result:req-789"
        payload = json.loads(payload_str)
        assert payload["action"] == "approve"
        assert payload["approver"] == "bob"
        assert payload["note"] == "Approved by risk team"

        # Verify 24-hour TTL
        mock_redis.expire.assert_called_once()
        expire_key, ttl = mock_redis.expire.call_args[0]
        assert expire_key == "hitl_result:req-789"
        assert ttl == 86400

    @pytest.mark.asyncio
    async def test_publish_resolution_no_redis_does_not_raise(self) -> None:
        """publish_resolution completes without error when no Redis client."""
        gateway = HITLGateway()
        gateway._redis = None

        # Must not raise
        await gateway.publish_resolution(
            request_id="req-000",
            action="deny",
            approver="system",
            note="No Redis available",
        )

    @pytest.mark.asyncio
    async def test_publish_then_wait_roundtrip(self) -> None:
        """Simulate publish → wait roundtrip via a shared mock."""
        payload = {"action": "approve", "approver": "dana", "note": "OK"}
        published: list[bytes] = []

        # Capture what was published so blpop can return it
        async def fake_rpush(key: str, value: str) -> int:
            published.append(value.encode() if isinstance(value, str) else value)
            return 1

        async def fake_blpop(key: str, timeout: float = 0) -> tuple | None:
            if published:
                return (key.encode(), published.pop(0))
            return None

        mock_redis = AsyncMock()
        mock_redis.rpush = AsyncMock(side_effect=fake_rpush)
        mock_redis.expire = AsyncMock(return_value=True)
        mock_redis.blpop = AsyncMock(side_effect=fake_blpop)

        gateway = HITLGateway()
        gateway._redis = mock_redis

        await gateway.publish_resolution(
            request_id="req-rt",
            action="approve",
            approver="dana",
            note="OK",
        )

        result = await gateway._wait_for_result("req-rt", timeout=5.0)

        assert result is not None
        assert result["approver"] == "dana"
