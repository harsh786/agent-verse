"""Trigger event bus on Redis Streams (TRG-18).

Trigger events (goal lifecycle, HITL decisions, memory creation, custom /
conversational / state-machine events) used to travel over Redis pub/sub, which
is at-most-once: an event published while a consumer was down — a deploy, a
Redis failover, a supervisor restart — was simply gone, so chain, HITL, memory
and EVENT-family triggers silently missed firings.

Every publisher now goes through :func:`publish_trigger_event` (or
:func:`publish_trigger_event_sync` from sync worker code). It ``XADD``\\ s the
event to one capped Redis Stream per event family::

    goal.completed / goal.failed / goal.score_below  -> trigger_bus_stream_goal
    hitl.approved / hitl.rejected                    -> trigger_bus_stream_hitl
    memory.created                                   -> trigger_bus_stream_memory
    trigger:event:*                                  -> trigger_bus_stream_event

Each entry carries ``channel`` (the legacy pub/sub channel name) and ``data``
(the JSON payload, passed through untouched). Streams are capped with
``MAXLEN ~ trigger_bus_stream_maxlen``.

Consumers read with one consumer group per consumer type (``XREADGROUP``), so
each event is handled once per consumer type however many replicas run, and
``XACK`` only after the handler (the governed dispatcher) accepted it. Entries
left pending by a crashed replica are ``XAUTOCLAIM``\\ ed after
``trigger_bus_claim_idle_ms`` and retried; the dispatcher's idempotency keys
make a retried entry fire each trigger at most once.

Rollout (one release of dual publish):

1. Deploy with ``TRIGGER_BUS_DUAL_PUBLISH=true`` (the default). New replicas
   XADD *and* PUBLISH, so replicas still running the old pub/sub consumers keep
   receiving events from new publishers while new replicas consume the streams.
   A consumer group is created at the start of its stream, so events XADDed
   before the first stream consumer started are processed too (duplicates with
   the pub/sub path are absorbed by the dispatcher's dedup).
2. Once every replica (API and Celery workers) runs this code, all consumers
   read the streams.
3. Set ``TRIGGER_BUS_DUAL_PUBLISH=false`` (next release) to stop the redundant
   pub/sub traffic.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.core.config import Settings, get_settings

_log = logging.getLogger(__name__)

EVENT_CHANNEL_PREFIX = "trigger:event:"

_GOAL_CHANNELS = frozenset({"goal.completed", "goal.failed", "goal.score_below"})
_HITL_CHANNELS = frozenset({"hitl.approved", "hitl.rejected"})
_MEMORY_CHANNELS = frozenset({"memory.created"})


class TriggerBusPublishError(RuntimeError):
    """The event could not be appended to its trigger stream."""


def dead_letter_stream(stream: str) -> str:
    """Where entries that exhausted ``trigger_bus_max_deliveries`` are kept."""
    return f"{stream}:dlq"


def stream_for_channel(channel: str, settings: Settings | None = None) -> str:
    """The Redis Stream holding events published on the legacy *channel*."""
    s = settings or get_settings()
    if channel in _GOAL_CHANNELS:
        return s.trigger_bus_stream_goal
    if channel in _HITL_CHANNELS:
        return s.trigger_bus_stream_hitl
    if channel in _MEMORY_CHANNELS:
        return s.trigger_bus_stream_memory
    if channel.startswith(EVENT_CHANNEL_PREFIX) and len(channel) > len(EVENT_CHANNEL_PREFIX):
        return s.trigger_bus_stream_event
    raise ValueError(f"{channel!r} is not a trigger-bus channel")


def _encode(payload: Mapping[str, Any] | str) -> str:
    return payload if isinstance(payload, str) else json.dumps(dict(payload))


def _entry_id(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def publish_trigger_event(redis: Any, channel: str, payload: Mapping[str, Any] | str) -> str:
    """Append a trigger event to its family stream (and dual-publish it).

    *payload* is a dict (JSON-encoded here, keys untouched) or an already
    encoded JSON string. Works with async and sync Redis clients. Returns the
    stream entry id; raises :class:`TriggerBusPublishError` when the XADD fails
    (the pub/sub copy is still attempted so old consumers are not starved).
    """
    s = get_settings()
    stream = stream_for_channel(channel, s)
    data = _encode(payload)
    xadd_error: Exception | None = None
    entry_id = ""
    try:
        entry_id = _entry_id(
            await _maybe_await(
                redis.xadd(
                    stream,
                    {"channel": channel, "data": data},
                    maxlen=s.trigger_bus_stream_maxlen,
                    approximate=True,
                )
            )
        )
    except Exception as exc:
        xadd_error = exc
    if s.trigger_bus_dual_publish:
        try:
            await _maybe_await(redis.publish(channel, data))
        except Exception as exc:
            _log.warning("trigger_bus_dual_publish_failed channel=%s: %s", channel, exc)
    if xadd_error is not None:
        raise TriggerBusPublishError(
            f"XADD to {stream} failed for {channel}: {xadd_error}"
        ) from xadd_error
    return entry_id


def publish_trigger_event_sync(redis: Any, channel: str, payload: Mapping[str, Any] | str) -> str:
    """:func:`publish_trigger_event` for sync Redis clients (Celery worker code)."""
    s = get_settings()
    stream = stream_for_channel(channel, s)
    data = _encode(payload)
    xadd_error: Exception | None = None
    entry_id = ""
    try:
        entry_id = _entry_id(
            redis.xadd(
                stream,
                {"channel": channel, "data": data},
                maxlen=s.trigger_bus_stream_maxlen,
                approximate=True,
            )
        )
    except Exception as exc:
        xadd_error = exc
    if s.trigger_bus_dual_publish:
        try:
            redis.publish(channel, data)
        except Exception as exc:
            _log.warning("trigger_bus_dual_publish_failed channel=%s: %s", channel, exc)
    if xadd_error is not None:
        raise TriggerBusPublishError(
            f"XADD to {stream} failed for {channel}: {xadd_error}"
        ) from xadd_error
    return entry_id


# ── Consumer side ─────────────────────────────────────────────────────────────

Handler = Callable[[dict[str, Any]], Awaitable[None]]

# Upper bound on XAUTOCLAIM batches per reclaim pass, so a large backlog of
# stale entries cannot starve new reads.
_MAX_CLAIM_BATCHES = 10
# Group consumers idle this long with no pending entries are deleted (B2-8).
_STALE_CONSUMER_IDLE_MS = 3_600_000


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return "" if value is None else str(value)


def default_consumer_name() -> str:
    """A consumer name unique to this process (host + pid + random suffix)."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


class TriggerStreamReader:
    """Reads one trigger stream through a consumer group.

    ``run`` delivers each entry to the handler as a pub/sub-shaped message
    (``{"type": "message", "channel", "data", "id"}``) and XACKs it only after
    the handler returned. A handler exception leaves the entry pending; pending
    entries idle for ``trigger_bus_claim_idle_ms`` (this consumer's failures or
    a crashed replica's in-flight entries) are XAUTOCLAIMed and retried, and an
    entry delivered more than ``trigger_bus_max_deliveries`` times is acked and
    dropped with an error log so one poison event cannot wedge the group.

    Redis errors propagate out of ``run``: the consumer's ``start`` returns and
    ``TriggerConsumerSupervisor`` restarts it with backoff (TRG-17), which also
    re-creates the group if the stream vanished (e.g. a flushed Redis).
    """

    def __init__(
        self,
        redis: Any,
        *,
        stream: str,
        group: str,
        consumer: str | None = None,
        settings: Settings | None = None,
    ) -> None:
        s = settings or get_settings()
        self.redis = redis
        self.stream = stream
        self.group = group
        self.consumer = consumer or default_consumer_name()
        self._block_ms = max(1, int(s.trigger_bus_block_ms))
        self._count = max(1, int(s.trigger_bus_read_count))
        self._claim_idle_ms = max(0, int(s.trigger_bus_claim_idle_ms))
        self._max_deliveries = max(1, int(s.trigger_bus_max_deliveries))
        # Look for stale pending entries twice per idle threshold.
        self._claim_interval_s = self._claim_idle_ms / 2000
        # A consumer idle this long with nothing pending is a dead process (B2-8).
        self._stale_consumer_idle_ms = _STALE_CONSUMER_IDLE_MS

    async def ensure_group(self) -> None:
        """Create the group at the start of the stream (idempotent).

        Starting at ``0`` rather than ``$`` means entries XADDed before the
        group first existed (the first deploy's dual-publish window) are
        processed too; after that the group's position persists in Redis.
        """
        try:
            await self.redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        await self.prune_stale_consumers()

    async def prune_stale_consumers(self) -> int:
        """Delete group consumers idle past the threshold with no pending entries.

        Every process joins under a fresh name, and Redis keeps a consumer
        forever, so each restart or deploy added one dead consumer per group
        (B2-8). A consumer still holding pending entries is kept (XAUTOCLAIM
        recovers them). Best effort: a failure never blocks joining.
        """
        removed = 0
        try:
            consumers = await self.redis.xinfo_consumers(self.stream, self.group)
            for info in consumers or []:
                name = _text(info.get("name"))
                if not name or name == self.consumer:
                    continue
                if int(info.get("pending", 0) or 0) > 0:
                    continue
                if int(info.get("idle", 0) or 0) < self._stale_consumer_idle_ms:
                    continue
                await self.redis.xgroup_delconsumer(self.stream, self.group, name)
                removed += 1
        except Exception as exc:
            _log.warning("trigger_bus_consumer_prune_failed group=%s: %s", self.group, exc)
        if removed:
            _log.info("trigger_bus_consumers_pruned group=%s removed=%d", self.group, removed)
        return removed

    async def run(self, handler: Handler, is_running: Callable[[], bool]) -> None:
        await self.ensure_group()
        loop = asyncio.get_running_loop()
        next_claim = loop.time()  # reclaim right away: recover a crashed replica's entries
        while is_running():
            if loop.time() >= next_claim:
                await self.reclaim(handler)
                next_claim = loop.time() + self._claim_interval_s
            started = loop.time()
            response = await self.redis.xreadgroup(
                self.group,
                self.consumer,
                {self.stream: ">"},
                count=self._count,
                block=self._block_ms,
            )
            entries = self._entries(response)
            for entry_id, fields in entries:
                await self._process(entry_id, fields, handler)
            if not entries and (loop.time() - started) * 1000 < self._block_ms / 2:
                # The client returned at once instead of blocking: don't spin.
                await asyncio.sleep(min(self._block_ms / 1000, 0.05))

    async def reclaim(self, handler: Handler) -> int:
        """XAUTOCLAIM entries pending longer than the idle threshold and retry them."""
        start = "0-0"
        handled = 0
        for _ in range(_MAX_CLAIM_BATCHES):
            result = await self.redis.xautoclaim(
                self.stream,
                self.group,
                self.consumer,
                min_idle_time=self._claim_idle_ms,
                start_id=start,
                count=self._count,
            )
            if not result:
                break
            next_start = _text(result[0])
            for raw_id, fields in result[1] or []:
                entry_id = _text(raw_id)
                if not fields:  # trimmed by MAXLEN while pending
                    await self._ack(entry_id)
                    continue
                deliveries = await self._deliveries(entry_id)
                if deliveries > self._max_deliveries:
                    # TRG-55: dead-letter (kept for replay) rather than drop.
                    await self._dead_letter(entry_id, fields, deliveries)
                    await self._ack(entry_id)
                    continue
                await self._process(entry_id, fields, handler)
                handled += 1
            if next_start in ("0-0", "0", ""):
                break
            start = next_start
        return handled

    async def _deliveries(self, entry_id: str) -> int:
        try:
            rows = await self.redis.xpending_range(
                self.stream, self.group, min=entry_id, max=entry_id, count=1
            )
        except Exception as exc:  # unknown count: keep retrying rather than drop
            _log.warning("trigger_bus_pending_lookup_failed id=%s: %s", entry_id, exc)
            return 0
        if not rows:
            return 0
        row = rows[0]
        count = row.get("times_delivered", 0) if isinstance(row, dict) else 0
        return int(count or 0)

    async def _process(self, entry_id: Any, fields: Any, handler: Handler) -> None:
        entry = _text(entry_id)
        items = fields.items() if isinstance(fields, Mapping) else []
        decoded = {_text(k): _text(v) for k, v in items}
        channel = decoded.get("channel", "")
        data = decoded.get("data")
        if not channel or data is None:
            _log.error("trigger_bus_malformed_entry stream=%s id=%s", self.stream, entry)
            await self._ack(entry)
            return
        message = {"type": "message", "channel": channel, "data": data, "id": entry}
        try:
            await handler(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning(
                "trigger_bus_handler_failed stream=%s group=%s id=%s (left pending): %s",
                self.stream,
                self.group,
                entry,
                exc,
            )
            return
        await self._ack(entry)

    async def _dead_letter(self, entry_id: str, fields: Any, deliveries: int) -> None:
        """Copy an entry that kept failing to ``<stream>:dlq`` before it is acked.

        It used to be acked and dropped with only a log line, so an event that
        failed for longer than the retry budget (e.g. a long DB outage) was lost.
        The dead-letter stream keeps the original channel/data plus where it came
        from, for inspection and replay. If the XADD itself fails the entry is
        NOT acked (it stays pending and is retried).
        """
        items = fields.items() if isinstance(fields, Mapping) else []
        record = {_text(k): _text(v) for k, v in items}
        record.update(
            {
                "source_stream": self.stream,
                "group": self.group,
                "source_id": entry_id,
                "deliveries": str(deliveries),
            }
        )
        _log.error(
            "trigger_bus_entry_dead_lettered stream=%s group=%s id=%s deliveries=%d",
            self.stream,
            self.group,
            entry_id,
            deliveries,
        )
        await self.redis.xadd(dead_letter_stream(self.stream), record, maxlen=100_000)

    async def _ack(self, entry_id: str) -> None:
        await self.redis.xack(self.stream, self.group, entry_id)

    @staticmethod
    def _entries(response: Any) -> list[tuple[Any, Any]]:
        if not response:
            return []
        if isinstance(response, Mapping):  # RESP3: {stream: [entries]}
            batches: list[Any] = list(response.values())
        else:  # RESP2: [[stream, [entries]], ...]
            batches = [item[1] for item in response]
        out: list[tuple[Any, Any]] = []
        for batch in batches:
            if batch and isinstance(batch[0], list | tuple) and len(batch[0]) == 2:
                out.extend((entry[0], entry[1]) for entry in batch)
            else:
                for inner in batch or []:
                    out.extend((entry[0], entry[1]) for entry in inner or [])
        return out


async def run_stream_consumer(consumer: Any, *, label: str, channel: str, group: str) -> None:
    """Shared ``start()`` body of the trigger consumers.

    Reads the stream holding *channel*'s family with *group* (one group per
    consumer type) and feeds entries to ``consumer._handle`` while
    ``consumer._running``. The reader — and so the consumer name — is kept on
    the consumer across supervisor restarts.
    """
    reader: TriggerStreamReader | None = getattr(consumer, "_stream_reader", None)
    if reader is None or reader.redis is not consumer._redis:
        reader = TriggerStreamReader(
            consumer._redis, stream=stream_for_channel(channel), group=group
        )
        consumer._stream_reader = reader
    _log.info(
        "%s_started stream=%s group=%s consumer=%s",
        label,
        reader.stream,
        reader.group,
        reader.consumer,
    )
    await reader.run(consumer._handle, lambda: bool(consumer._running))


__all__ = [
    "EVENT_CHANNEL_PREFIX",
    "TriggerBusPublishError",
    "TriggerStreamReader",
    "default_consumer_name",
    "publish_trigger_event",
    "publish_trigger_event_sync",
    "run_stream_consumer",
    "stream_for_channel",
]
