"""Tests for app/triggers/dlq.py — trigger dead-letter-queue writer."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.triggers.dlq import FAILURE_TYPES, RETRY_DELAYS, write_to_dlq


class TestConstants:
    def test_failure_types_contains_expected_values(self) -> None:
        for expected in [
            "SIGNATURE_INVALID",
            "PAYLOAD_TOO_LARGE",
            "CONDITION_ERROR",
            "TEMPLATE_RENDER_ERROR",
            "GOAL_ENQUEUE_FAILED",
            "RATE_LIMITED",
            "DEDUP_BLOCKED",
            "BULKHEAD_FULL",
            "CIRCUIT_OPEN",
            "QUOTA_EXCEEDED",
            "RBAC_DENIED",
        ]:
            assert expected in FAILURE_TYPES

    def test_retry_delays_are_exponential(self) -> None:
        assert RETRY_DELAYS == [5, 15, 45]


class TestWriteToDlq:
    @pytest.mark.asyncio
    async def test_no_db_session_is_noop(self) -> None:
        # should not raise
        await write_to_dlq(
            None,
            tenant_id="t1",
            trigger_id="trig-1",
            failure_type="RATE_LIMITED",
            error_message="too many",
            raw_payload={"foo": "bar"},
        )

    @pytest.mark.asyncio
    async def test_writes_row_and_commits(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()

        await write_to_dlq(
            session,
            tenant_id="tenant-1",
            trigger_id="trig-1",
            failure_type="SIGNATURE_INVALID",
            error_message="bad sig",
            raw_payload={"a": 1},
            retry_count=2,
        )

        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()
        args, kwargs = session.execute.call_args
        params = args[1]
        assert params["tenant_id"] == "tenant-1"
        assert params["trigger_id"] == "trig-1"
        assert params["failure_type"] == "SIGNATURE_INVALID"
        assert params["retry_count"] == 2
        assert json.loads(params["raw_payload"]) == {"a": 1}

    @pytest.mark.asyncio
    async def test_error_message_is_truncated_to_2048_chars(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        long_message = "x" * 5000

        await write_to_dlq(
            session,
            tenant_id="t1",
            trigger_id="trig-1",
            failure_type="CONDITION_ERROR",
            error_message=long_message,
            raw_payload={},
        )

        args, _ = session.execute.call_args
        params = args[1]
        assert len(params["error_message"]) == 2048

    @pytest.mark.asyncio
    async def test_db_error_is_caught_not_raised(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=RuntimeError("db connection lost"))
        session.commit = AsyncMock()

        # should not raise despite the execute failure
        await write_to_dlq(
            session,
            tenant_id="t1",
            trigger_id="trig-1",
            failure_type="GOAL_ENQUEUE_FAILED",
            error_message="oops",
            raw_payload={},
        )
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_default_retry_count_is_zero(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()

        await write_to_dlq(
            session,
            tenant_id="t1",
            trigger_id="trig-1",
            failure_type="QUOTA_EXCEEDED",
            error_message="over quota",
            raw_payload={},
        )
        args, _ = session.execute.call_args
        assert args[1]["retry_count"] == 0
