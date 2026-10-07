"""The proactive daily cap — a consent control, so it must hold across replicas.

``ProactiveEngine`` asks a :class:`DailyCap` to *reserve* one outreach slot for a
(tenant, principal, local day) before delivering, and releases it when delivery
failed. Reserving is atomic (Redis ``INCR``, or a dict under the event loop), so
concurrent signals on any number of replicas cannot overshoot ``max_per_day``.

* :class:`RedisDailyCap` — the shared counter (``INCR`` + ``EXPIRE`` in one
  transaction; a reservation past the limit is undone with ``DECR``).
* :class:`InMemoryDailyCap` — per-process; only for tests and a dev run without
  Redis.
* :class:`AppStateDailyCap` — what the app wires: resolves ``app.state._redis``
  on every call (the lifespan sets it after the routers are built) and falls back
  to the in-memory cap only outside production. In production with no Redis, or
  when Redis errors, it raises :class:`DailyCapUnavailableError` and the engine
  sends nothing (fail closed) — before a10-F227-01 the wired engine always used a
  per-replica dict, so N replicas allowed N x the cap and a restart reset it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

import structlog

_log = structlog.get_logger(__name__)

# A day key outlives its day in every timezone, then expires.
_KEY_TTL_SECONDS = 2 * 86_400


class DailyCapUnavailableError(RuntimeError):
    """The shared counter cannot be read or written; send nothing."""


class DailyCap(Protocol):
    async def reserve(self, tenant_id: str, principal_id: str, day: str, limit: int) -> bool:
        """Take one slot for the day; False when ``limit`` slots are already taken."""
        ...

    async def release(self, tenant_id: str, principal_id: str, day: str) -> None:
        """Give back a slot taken by :meth:`reserve` (nothing was delivered)."""
        ...


def cap_key(tenant_id: str, principal_id: str, day: str) -> str:
    # Hashed so neither id can forge another tenant's key (both are free text).
    digest = hashlib.sha256(json.dumps([tenant_id, principal_id]).encode()).hexdigest()[:32]
    return f"proactive:sent:{digest}:{day}"


class InMemoryDailyCap:
    """Per-process counter (tests / dev without Redis). Not shared, not durable."""

    def __init__(self) -> None:
        self._sent: dict[tuple[str, str, str], int] = {}

    async def reserve(self, tenant_id: str, principal_id: str, day: str, limit: int) -> bool:
        key = (tenant_id, principal_id, day)
        used = self._sent.get(key, 0)
        if used >= limit:
            return False
        self._sent[key] = used + 1
        return True

    async def release(self, tenant_id: str, principal_id: str, day: str) -> None:
        key = (tenant_id, principal_id, day)
        if self._sent.get(key, 0) > 0:
            self._sent[key] -= 1

    def sent(self, tenant_id: str, principal_id: str, day: str) -> int:
        return self._sent.get((tenant_id, principal_id, day), 0)


class RedisDailyCap:
    """The shared counter: one Redis key per (tenant, principal, day)."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def reserve(self, tenant_id: str, principal_id: str, day: str, limit: int) -> bool:
        key = cap_key(tenant_id, principal_id, day)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.incr(key)
                pipe.expire(key, _KEY_TTL_SECONDS)
                used, _ = await pipe.execute()
            if int(used) <= limit:
                return True
            await self._redis.decr(key)
            return False
        except Exception as exc:
            raise DailyCapUnavailableError(f"proactive daily cap: {type(exc).__name__}") from exc

    async def release(self, tenant_id: str, principal_id: str, day: str) -> None:
        try:
            await self._redis.decr(cap_key(tenant_id, principal_id, day))
        except Exception as exc:
            # The slot stays used: the principal may get one message fewer today.
            _log.warning("proactive_cap_release_failed", error_type=type(exc).__name__)


class AppStateDailyCap:
    """The wired cap: Redis from ``app.state._redis``, resolved per call."""

    def __init__(self, state: Any) -> None:
        self._state = state
        self._fallback = InMemoryDailyCap()

    def _cap(self) -> DailyCap:
        redis = getattr(self._state, "_redis", None)
        if redis is not None:
            return RedisDailyCap(redis)
        from app.core.config import get_settings

        if get_settings().is_production:
            raise DailyCapUnavailableError("proactive daily cap: no shared Redis counter")
        return self._fallback

    async def reserve(self, tenant_id: str, principal_id: str, day: str, limit: int) -> bool:
        return await self._cap().reserve(tenant_id, principal_id, day, limit)

    async def release(self, tenant_id: str, principal_id: str, day: str) -> None:
        try:
            cap = self._cap()
        except DailyCapUnavailableError:
            return
        await cap.release(tenant_id, principal_id, day)
