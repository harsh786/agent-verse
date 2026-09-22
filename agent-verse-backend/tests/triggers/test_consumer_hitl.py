"""Tests for app/triggers/consumers/hitl.py — HITL approval/rejection trigger consumer."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.triggers.consumers.hitl import HITLTriggerConsumer


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_without_redis_logs_and_returns(self) -> None:
        consumer = HITLTriggerConsumer(redis=None)
        await consumer.start()
        assert consumer._running is False

    @pytest.mark.asyncio
    async def test_start_subscribes_and_processes_messages(self) -> None:
        redis = MagicMock()
        pubsub = MagicMock()
        redis.pubsub.return_value = pubsub
        pubsub.subscribe = AsyncMock()

        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "channel": "hitl.approved", "data": json.dumps({"tenant_id": "t1"})},
        ]

        async def fake_listen():
            for m in messages:
                yield m

        pubsub.listen = fake_listen

        consumer = HITLTriggerConsumer(redis=redis)
        handled = []

        async def fake_handle(msg):
            handled.append(msg)
            consumer._running = False

        consumer._handle = fake_handle  # type: ignore[method-assign]
        await consumer.start()

        pubsub.subscribe.assert_awaited_once_with("hitl.approved", "hitl.rejected")
        assert len(handled) == 1

    @pytest.mark.asyncio
    async def test_start_swallows_exceptions(self) -> None:
        redis = MagicMock()
        redis.pubsub.side_effect = RuntimeError("boom")
        consumer = HITLTriggerConsumer(redis=redis)
        await consumer.start()  # should not raise

    @pytest.mark.asyncio
    async def test_stop_clears_running(self) -> None:
        consumer = HITLTriggerConsumer(redis=MagicMock())
        consumer._running = True
        await consumer.stop()
        assert consumer._running is False


class TestHandle:
    @pytest.mark.asyncio
    async def test_malformed_json_is_ignored(self) -> None:
        consumer = HITLTriggerConsumer(
            trigger_store=AsyncMock(), dispatcher=AsyncMock(), redis=MagicMock()
        )
        await consumer._handle({"channel": "hitl.approved", "data": b"not-json"})
        consumer._store.find_by_type_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_tenant_id_is_ignored(self) -> None:
        store = AsyncMock()
        dispatcher = AsyncMock()
        consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=MagicMock())
        await consumer._handle(
            {"channel": "hitl.approved", "data": json.dumps({})}
        )
        store.find_by_type_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_store_or_dispatcher_is_ignored(self) -> None:
        consumer = HITLTriggerConsumer(redis=MagicMock())
        # should not raise even with tenant_id present
        await consumer._handle(
            {"channel": "hitl.approved", "data": json.dumps({"tenant_id": "t1"})}
        )

    @pytest.mark.asyncio
    async def test_dispatches_matching_triggers_for_approved_channel(self) -> None:
        store = AsyncMock()
        spec = SimpleNamespace(hitl_queue_id="")
        store.find_by_type_async = AsyncMock(return_value=[{"spec": spec}])
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()

        consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=MagicMock())
        await consumer._handle(
            {
                "channel": b"hitl.approved",
                "data": json.dumps({"tenant_id": "t1", "tenant_plan": "pro"}).encode(),
            }
        )

        store.find_by_type_async.assert_awaited_once_with("hitl_approved", tenant_id="t1")
        dispatcher.dispatch.assert_awaited_once()
        call_spec, call_data, call_ctx = dispatcher.dispatch.call_args.args
        assert call_spec is spec
        assert call_ctx.tenant_id == "t1"
        assert call_ctx.plan == "pro"

    @pytest.mark.asyncio
    async def test_rejected_channel_uses_rejected_trigger_type(self) -> None:
        store = AsyncMock()
        store.find_by_type_async = AsyncMock(return_value=[])
        dispatcher = AsyncMock()
        consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=MagicMock())
        await consumer._handle(
            {"channel": "hitl.rejected", "data": json.dumps({"tenant_id": "t1"})}
        )
        store.find_by_type_async.assert_awaited_once_with("hitl_rejected", tenant_id="t1")

    @pytest.mark.asyncio
    async def test_queue_id_mismatch_skips_dispatch(self) -> None:
        store = AsyncMock()
        spec = SimpleNamespace(hitl_queue_id="queue-A")
        store.find_by_type_async = AsyncMock(return_value=[{"spec": spec}])
        dispatcher = AsyncMock()

        consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=MagicMock())
        await consumer._handle(
            {
                "channel": "hitl.approved",
                "data": json.dumps({"tenant_id": "t1", "hitl_queue_id": "queue-B"}),
            }
        )
        dispatcher.dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_queue_id_match_dispatches(self) -> None:
        store = AsyncMock()
        spec = SimpleNamespace(hitl_queue_id="queue-A")
        store.find_by_type_async = AsyncMock(return_value=[{"spec": spec}])
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()

        consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=MagicMock())
        await consumer._handle(
            {
                "channel": "hitl.approved",
                "data": json.dumps({"tenant_id": "t1", "hitl_queue_id": "queue-A"}),
            }
        )
        dispatcher.dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_trigger_without_spec_key_uses_trigger_itself(self) -> None:
        store = AsyncMock()
        trigger_dict = {"hitl_queue_id": ""}
        store.find_by_type_async = AsyncMock(return_value=[trigger_dict])
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()

        consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=MagicMock())
        await consumer._handle(
            {"channel": "hitl.approved", "data": json.dumps({"tenant_id": "t1"})}
        )
        dispatcher.dispatch.assert_awaited_once()
        call_spec = dispatcher.dispatch.call_args.args[0]
        assert call_spec is trigger_dict

    @pytest.mark.asyncio
    async def test_dispatch_exception_is_caught_and_logged(self) -> None:
        store = AsyncMock()
        spec = SimpleNamespace(hitl_queue_id="")
        store.find_by_type_async = AsyncMock(return_value=[{"spec": spec}])
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("dispatch failed"))

        consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=MagicMock())
        # should not raise
        await consumer._handle(
            {"channel": "hitl.approved", "data": json.dumps({"tenant_id": "t1"})}
        )

    @pytest.mark.asyncio
    async def test_multiple_triggers_all_dispatched(self) -> None:
        store = AsyncMock()
        spec1 = SimpleNamespace(hitl_queue_id="")
        spec2 = SimpleNamespace(hitl_queue_id="")
        store.find_by_type_async = AsyncMock(return_value=[{"spec": spec1}, {"spec": spec2}])
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()

        consumer = HITLTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=MagicMock())
        await consumer._handle(
            {"channel": "hitl.approved", "data": json.dumps({"tenant_id": "t1"})}
        )
        assert dispatcher.dispatch.await_count == 2
