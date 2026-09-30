"""Test support: drive a trigger consumer over a fake Redis Stream (TRG-18)."""

from __future__ import annotations

import asyncio
from typing import Any

import fakeredis

from app.triggers import bus


def stream_redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


async def publish(redis: Any, channel: str, data: dict[str, Any] | str) -> str:
    """Append one event exactly as production publishers do."""
    return await bus.publish_trigger_event(redis, channel, data)


async def drain(consumer: Any, *, timeout: float = 3.0) -> None:
    """Run ``consumer.start()`` until its group has delivered and acked every
    entry already in its stream, then stop it."""
    task = asyncio.create_task(consumer.start())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while True:
            await asyncio.sleep(0.01)
            if task.done():
                break
            reader = getattr(consumer, "_stream_reader", None)
            if reader is None:
                continue
            entries = await reader.redis.xrange(reader.stream)
            groups = await reader.redis.xinfo_groups(reader.stream)
            group = next((g for g in groups if g["name"] == reader.group), None)
            last = entries[-1][0] if entries else "0-0"
            if group is not None and group["last-delivered-id"] == last and not group["pending"]:
                break  # every entry delivered and acked (handled)
            if loop.time() > deadline:
                raise AssertionError("consumer did not drain its stream")
    finally:
        await consumer.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
