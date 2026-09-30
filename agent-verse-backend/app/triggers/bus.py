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

import inspect
import json
import logging
from collections.abc import Mapping
from typing import Any

from app.core.config import Settings, get_settings

_log = logging.getLogger(__name__)

EVENT_CHANNEL_PREFIX = "trigger:event:"

_GOAL_CHANNELS = frozenset({"goal.completed", "goal.failed", "goal.score_below"})
_HITL_CHANNELS = frozenset({"hitl.approved", "hitl.rejected"})
_MEMORY_CHANNELS = frozenset({"memory.created"})


class TriggerBusPublishError(RuntimeError):
    """The event could not be appended to its trigger stream."""


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


__all__ = [
    "EVENT_CHANNEL_PREFIX",
    "TriggerBusPublishError",
    "publish_trigger_event",
    "publish_trigger_event_sync",
    "stream_for_channel",
]
