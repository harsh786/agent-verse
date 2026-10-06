"""B2-8: dead consumers are pruned from the trigger-bus groups.

Every API process joins each consumer group under a fresh name
(host:pid:random). Redis never forgets a consumer, so every restart / deploy /
supervisor restart added one per group forever (live: 45 names in the EVENT
group of a one-replica stack). A consumer idle past the threshold with NO
pending entries is now deleted when a reader (re)joins; one that still holds
pending entries is kept, so XAUTOCLAIM can recover them.
"""

from __future__ import annotations

from app.triggers.bus import TriggerStreamReader
from tests.triggers.stream_support import stream_redis


async def test_idle_consumers_without_pending_entries_are_pruned() -> None:
    redis = stream_redis()
    stream, group = "trigger:stream:event", "trigger-consumer:event"
    await redis.xgroup_create(stream, group, id="0", mkstream=True)
    await redis.xadd(stream, {"channel": "trigger:event:x", "data": "{}"})
    # 'holder' read the entry and never acked it; 'idle' read nothing.
    await redis.xreadgroup(group, "holder", {stream: ">"}, count=1)
    await redis.xreadgroup(group, "idle", {stream: ">"}, count=1)

    reader = TriggerStreamReader(redis, stream=stream, group=group, consumer="me")
    reader._stale_consumer_idle_ms = 0
    await reader.ensure_group()

    names = {c["name"] for c in await redis.xinfo_consumers(stream, group)}
    assert "idle" not in names
    assert "holder" in names  # its pending entry stays recoverable


async def test_prune_failure_never_blocks_joining() -> None:
    redis = stream_redis()

    async def boom(*_: object, **__: object) -> list[object]:
        raise ConnectionError("xinfo unsupported")

    redis.xinfo_consumers = boom  # type: ignore[method-assign]
    reader = TriggerStreamReader(redis, stream="s", group="g", consumer="me")
    await reader.ensure_group()  # does not raise
