"""Per-tenant structured log store behind ``GET /observability/logs``.

Fed by the ``feed_tenant_log_store`` structlog processor
(``app/observability/logging.py``): every log line emitted while a tenant is bound
is recorded here. Two backends:

* **Redis Streams** (``av:logs:{tenant_id}``) once ``set_redis`` is called from the
  API lifespan — shared across API replicas, bounded by ``MAXLEN ~`` and expired
  after ``STREAM_TTL_S`` of inactivity. Writes are fire-and-forget tasks scheduled
  on the event loop that owns the client, capped at ``max_inflight`` (excess lines
  are shed and counted, never queued without bound).
* **In-memory** otherwise — bounded per tenant *and* in the number of tenants, and
  explicitly process-local (the API reports ``source: "memory"``).

The recording path (``record_nowait``) is synchronous, thread-safe and never
raises. The read path (``query`` / ``stream_new_since``) raises on backend errors so
the API can answer 503 instead of pretending there are no logs.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading
import time
from collections import OrderedDict, deque
from datetime import UTC, datetime
from typing import Any

# stdlib logger (NOT structlog) so a store failure can never feed back into the
# structlog processor that feeds this store.
_obs_log = logging.getLogger("observability.log_store")


def _decode(value: Any) -> Any:
    return value.decode() if isinstance(value, bytes) else value


class StructuredLogStore:
    STREAM_KEY = "av:logs:{tenant_id}"
    MAX_STREAM_LEN = 10_000  # entries per tenant stream (approximate trimming)
    STREAM_TTL_S = 7 * 24 * 3600  # an idle tenant's stream expires after a week
    MAX_FIELD_CHARS = 1_000

    def __init__(
        self,
        *,
        max_memory_per_tenant: int = 500,
        max_memory_tenants: int = 1_000,
        max_inflight: int = 1_000,
    ) -> None:
        self._redis: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._max_memory = max_memory_per_tenant
        self._max_tenants = max_memory_tenants
        self._max_inflight = max_inflight
        self._lock = threading.Lock()
        self._memory: OrderedDict[str, deque[dict[str, Any]]] = OrderedDict()
        self._seq = itertools.count(1)
        self._inflight = 0
        self._tasks: set[asyncio.Task[None]] = set()
        self._dropped = 0
        self._write_errors = 0

    # ── wiring ────────────────────────────────────────────────────────────────

    def set_redis(self, redis: Any) -> None:
        """Wire the async Redis client (API lifespan). Must be called on the loop
        that owns the client — log writes from any thread are marshalled onto it."""
        self._redis = redis
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None

    @property
    def backend(self) -> str:
        return "redis_stream" if self._redis is not None else "memory"

    def reset(self) -> None:
        """Unwire Redis and clear the in-memory buffers (tests / shutdown)."""
        with self._lock:
            self._redis = None
            self._loop = None
            self._memory.clear()
            self._inflight = 0
            self._dropped = 0
            self._write_errors = 0
        for name in ("query", "stream_new_since", "record_nowait"):
            # Undo instance-level monkeypatches so the class methods apply again.
            self.__dict__.pop(name, None)

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "inflight": self._inflight,
                "dropped": self._dropped,
                "write_errors": self._write_errors,
            }

    # ── recording (sync, never raises) ────────────────────────────────────────

    def record_nowait(self, tenant_id: str, entry: dict[str, Any]) -> None:
        """Record one entry without blocking. Safe from any thread; never raises."""
        try:
            entry = dict(entry)
            entry.setdefault("id", f"{int(time.time() * 1000)}-{next(self._seq)}")
            entry.setdefault("timestamp", datetime.now(UTC).isoformat())
            redis, loop = self._redis, self._loop
            if redis is not None and loop is not None and not loop.is_closed():
                with self._lock:
                    if self._inflight >= self._max_inflight:
                        self._dropped += 1
                        return
                    self._inflight += 1
                try:
                    loop.call_soon_threadsafe(self._spawn_write, redis, tenant_id, entry)
                except RuntimeError:  # loop closed between the check and the call
                    with self._lock:
                        self._inflight -= 1
                        self._dropped += 1
                return
            self._append_memory(tenant_id, entry)
        except Exception as exc:  # pragma: no cover — defensive: logging must not break
            _obs_log.debug("log_store_record_failed: %s", exc)

    def _append_memory(self, tenant_id: str, entry: dict[str, Any]) -> None:
        with self._lock:
            buf = self._memory.get(tenant_id)
            if buf is None:
                buf = deque(maxlen=self._max_memory)
                self._memory[tenant_id] = buf
                while len(self._memory) > self._max_tenants:
                    self._memory.popitem(last=False)
            else:
                self._memory.move_to_end(tenant_id)
            buf.append(entry)

    def _spawn_write(self, redis: Any, tenant_id: str, entry: dict[str, Any]) -> None:
        task = asyncio.ensure_future(self._write_redis(redis, tenant_id, entry))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _write_redis(self, redis: Any, tenant_id: str, entry: dict[str, Any]) -> None:
        try:
            await self._xadd(redis, tenant_id, entry)
        except Exception as exc:
            with self._lock:
                self._write_errors += 1
            _obs_log.debug("log_store_redis_write_failed: %s", exc)
        finally:
            with self._lock:
                self._inflight -= 1

    async def _xadd(self, redis: Any, tenant_id: str, entry: dict[str, Any]) -> None:
        key = self.STREAM_KEY.format(tenant_id=tenant_id)
        # Redis Streams require string values; drop empties to save space.
        fields = {k: str(v) for k, v in entry.items() if v not in (None, "")}
        await redis.xadd(key, fields, maxlen=self.MAX_STREAM_LEN, approximate=True)
        await redis.expire(key, self.STREAM_TTL_S)

    async def drain(self) -> None:
        """Await in-flight Redis writes (tests / graceful shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def emit(
        self,
        tenant_id: str,
        level: str,
        message: str,
        source: str = "",
        goal_id: str = "",
        **kwargs: Any,
    ) -> None:
        """Write one entry directly (awaited). Raises on a Redis error."""
        entry: dict[str, Any] = {
            "id": f"{int(time.time() * 1000)}-{next(self._seq)}",
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level.lower(),
            "message": message,
            "source": source,
            "goal_id": goal_id,
            **{k: str(v) for k, v in kwargs.items()},
        }
        if self._redis is not None:
            await self._xadd(self._redis, tenant_id, entry)
            return
        self._append_memory(tenant_id, entry)

    # ── reading (raises on backend errors) ────────────────────────────────────

    def memory_snapshot(self, tenant_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._memory.get(tenant_id, ()))

    def memory_tenants(self) -> list[str]:
        with self._lock:
            return list(self._memory)

    async def query(
        self, tenant_id: str, limit: int = 50, level: str | None = None
    ) -> list[dict[str, Any]]:
        """Newest-first entries for a tenant. Raises when the Redis backend fails."""
        if self._redis is not None:
            key = self.STREAM_KEY.format(tenant_id=tenant_id)
            # Over-fetch so a level filter can still fill `limit`.
            raw = await self._redis.xrevrange(key, count=limit * 4 if level else limit)
            logs: list[dict[str, Any]] = []
            for _stream_id, fields in raw:
                log = {_decode(k): _decode(v) for k, v in fields.items()}
                if level and log.get("level") != level.lower():
                    continue
                logs.append(log)
                if len(logs) >= limit:
                    break
            return logs

        buf = list(reversed(self.memory_snapshot(tenant_id)))
        if level:
            buf = [e for e in buf if e.get("level") == level.lower()]
        return buf[:limit]

    async def stream_new_since(self, tenant_id: str, last_id: str = "$") -> list[dict[str, Any]]:
        """Entries newer than *last_id* via ``XREAD BLOCK 2000`` (SSE tailing).

        Returns ``[]`` on timeout or when Redis is not wired. Raises on Redis
        errors so the SSE stream can surface them instead of going silent.
        """
        if self._redis is None:
            return []
        key = self.STREAM_KEY.format(tenant_id=tenant_id)
        raw = await self._redis.xread({key: last_id}, count=50, block=2000)
        logs: list[dict[str, Any]] = []
        for _key, msgs in raw or []:
            for stream_id, fields in msgs:
                log = {_decode(k): _decode(v) for k, v in fields.items()}
                log["_stream_id"] = _decode(stream_id)
                logs.append(log)
        return logs


# Module-level singleton — fed by the structlog processor, wired to Redis in the
# API lifespan (app/main.py), read by app/api/observability.py.
log_store = StructuredLogStore()
