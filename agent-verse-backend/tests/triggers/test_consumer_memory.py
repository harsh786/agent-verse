"""Tests for app/triggers/consumers/memory.py — memory-creation trigger consumer."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.triggers.consumers.memory import MemoryTriggerConsumer


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_without_redis_logs_and_returns(self) -> None:
        consumer = MemoryTriggerConsumer(redis=None)
        await consumer.start()
        assert consumer._running is False

    @pytest.mark.asyncio
    async def test_start_reads_the_stream_and_processes_messages(self) -> None:
        """TRG-18: the consumer reads its trigger stream via a consumer group."""
        import fakeredis

        from app.core.config import get_settings
        from app.triggers.bus import publish_trigger_event

        redis = fakeredis.FakeAsyncRedis(decode_responses=True)
        await publish_trigger_event(redis, "memory.created", {"tenant_id": "t1"})

        consumer = MemoryTriggerConsumer(redis=redis)
        handled = []

        async def fake_handle(msg):
            handled.append(msg)
            consumer._running = False

        consumer._handle = fake_handle  # type: ignore[method-assign]
        await consumer.start()

        assert len(handled) == 1
        assert handled[0]["channel"] == "memory.created"
        assert json.loads(handled[0]["data"]) == {"tenant_id": "t1"}
        stream = get_settings().trigger_bus_stream_memory
        assert (await redis.xpending(stream, MemoryTriggerConsumer.GROUP))["pending"] == 0

    @pytest.mark.asyncio
    async def test_start_swallows_exceptions(self) -> None:
        redis = MagicMock()
        redis.xgroup_create = AsyncMock(side_effect=RuntimeError("boom"))
        consumer = MemoryTriggerConsumer(redis=redis)
        await consumer.start()  # should not raise

    @pytest.mark.asyncio
    async def test_stop_clears_running(self) -> None:
        consumer = MemoryTriggerConsumer(redis=MagicMock())
        consumer._running = True
        await consumer.stop()
        assert consumer._running is False


class TestHandle:
    @pytest.mark.asyncio
    async def test_malformed_json_is_ignored(self) -> None:
        consumer = MemoryTriggerConsumer(
            trigger_store=AsyncMock(), dispatcher=AsyncMock(), redis=MagicMock()
        )
        await consumer._handle({"data": b"not-json"})
        consumer._store.find_by_type_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_tenant_id_is_ignored(self) -> None:
        store = AsyncMock()
        consumer = MemoryTriggerConsumer(trigger_store=store, dispatcher=AsyncMock(), redis=MagicMock())
        await consumer._handle({"data": json.dumps({})})
        store.find_by_type_async.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_store_or_dispatcher_is_ignored(self) -> None:
        consumer = MemoryTriggerConsumer(redis=MagicMock())
        await consumer._handle({"data": json.dumps({"tenant_id": "t1"})})

    @pytest.mark.asyncio
    async def test_dispatches_matching_triggers(self) -> None:
        store = AsyncMock()
        spec = SimpleNamespace(memory_type="")
        store.find_by_type_async = AsyncMock(return_value=[{"spec": spec}])
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()
        # The plan comes from the tenant record (TRG-05), never the event body.
        dispatcher.resolve_tenant_plan = AsyncMock(return_value="starter")

        consumer = MemoryTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=MagicMock())
        await consumer._handle(
            {"data": json.dumps({"tenant_id": "t1", "tenant_plan": "enterprise"}).encode()}
        )

        store.find_by_type_async.assert_awaited_once_with("memory_created", tenant_id="t1")
        dispatcher.dispatch.assert_awaited_once()
        call_spec, call_data, call_ctx = dispatcher.dispatch.call_args.args
        assert call_spec is spec
        assert call_ctx.tenant_id == "t1"
        assert call_ctx.plan == "starter"

    @pytest.mark.asyncio
    async def test_memory_type_mismatch_skips_dispatch(self) -> None:
        store = AsyncMock()
        spec = SimpleNamespace(memory_type="insight")
        store.find_by_type_async = AsyncMock(return_value=[{"spec": spec}])
        dispatcher = AsyncMock()

        consumer = MemoryTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=MagicMock())
        await consumer._handle(
            {"data": json.dumps({"tenant_id": "t1", "memory_type": "learning"})}
        )
        dispatcher.dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_memory_type_match_dispatches(self) -> None:
        store = AsyncMock()
        spec = SimpleNamespace(memory_type="insight")
        store.find_by_type_async = AsyncMock(return_value=[{"spec": spec}])
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()

        consumer = MemoryTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=MagicMock())
        await consumer._handle(
            {"data": json.dumps({"tenant_id": "t1", "memory_type": "insight"})}
        )
        dispatcher.dispatch.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatch_exception_is_caught_and_logged(self) -> None:
        store = AsyncMock()
        spec = SimpleNamespace(memory_type="")
        store.find_by_type_async = AsyncMock(return_value=[{"spec": spec}])
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock(side_effect=RuntimeError("dispatch failed"))

        consumer = MemoryTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=MagicMock())
        await consumer._handle({"data": json.dumps({"tenant_id": "t1"})})

    @pytest.mark.asyncio
    async def test_multiple_triggers_all_dispatched(self) -> None:
        store = AsyncMock()
        spec1 = SimpleNamespace(memory_type="")
        spec2 = SimpleNamespace(memory_type="")
        store.find_by_type_async = AsyncMock(return_value=[{"spec": spec1}, {"spec": spec2}])
        dispatcher = AsyncMock()
        dispatcher.dispatch = AsyncMock()

        consumer = MemoryTriggerConsumer(trigger_store=store, dispatcher=dispatcher, redis=MagicMock())
        await consumer._handle({"data": json.dumps({"tenant_id": "t1"})})
        assert dispatcher.dispatch.await_count == 2
