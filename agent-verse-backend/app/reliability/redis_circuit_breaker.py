"""Redis-backed circuit breaker — state shared across all worker replicas.

Each tool per tenant has its own set of circuit breaker keys in Redis:
  cb:{tenant_id}:{tool_name}:state    → "closed" | "open" | "half_open"
  cb:{tenant_id}:{tool_name}:failures → integer count
  cb:{tenant_id}:{tool_name}:opened_at → epoch float (monotonic)

A TTL of 2× the cooldown period is applied so stale keys self-expire.

Falls back to the in-memory :class:`~app.reliability.circuit_breaker.CircuitBreaker`
if Redis is unavailable, so a Redis outage never takes down the whole service.
"""
from __future__ import annotations

import time
from typing import Any

from app.reliability.circuit_breaker import CircuitBreaker, CircuitState


class RedisCircuitBreaker:
    """Circuit breaker backed by Redis for cross-replica state sharing.

    Args:
        redis_client: An ``redis.asyncio.Redis``-compatible async client.
        tenant_id:    Tenant owning this breaker.
        tool_name:    Tool (or service) this breaker guards.
        failure_threshold: Consecutive failures required to open the circuit.
        cooldown_seconds:  Seconds to wait before allowing a half-open probe.
    """

    def __init__(
        self,
        *,
        redis_client: Any,
        tenant_id: str,
        tool_name: str,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self._redis = redis_client
        self._tenant_id = tenant_id
        self._tool_name = tool_name
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._prefix = f"cb:{tenant_id}:{tool_name}"
        # In-memory fallback used when Redis is unreachable
        self._fallback = CircuitBreaker(
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
        )

    # ── key helpers ────────────────────────────────────────────────────────────

    def _key(self, suffix: str) -> str:
        return f"{self._prefix}:{suffix}"

    # ── async Redis-backed interface ───────────────────────────────────────────

    async def get_state(self) -> CircuitState:
        """Return the current circuit state from Redis."""
        try:
            state_str = await self._redis.get(self._key("state"))
            if state_str is None:
                return CircuitState.CLOSED
            return CircuitState(state_str)
        except Exception:
            return self._fallback.state

    async def can_call_async(self) -> bool:
        """Return True if a call is allowed now (checks Redis state).

        Handles the OPEN → HALF_OPEN transition after the cooldown expires.
        """
        try:
            state_str = await self._redis.get(self._key("state"))
            state = CircuitState(state_str) if state_str else CircuitState.CLOSED

            if state == CircuitState.CLOSED:
                return True

            if state == CircuitState.OPEN:
                opened_at_str = await self._redis.get(self._key("opened_at"))
                if opened_at_str:
                    opened_at = float(opened_at_str)
                    if time.monotonic() - opened_at >= self._cooldown:
                        # Promote to HALF_OPEN to allow a single probe call
                        await self._redis.set(
                            self._key("state"), CircuitState.HALF_OPEN.value
                        )
                        return True
                return False

            if state == CircuitState.HALF_OPEN:
                return True

            return False
        except Exception:
            return self._fallback.can_call()

    async def record_failure_async(self) -> None:
        """Record a failure.  Opens the circuit once ``failure_threshold`` is reached."""
        try:
            failures = await self._redis.incr(self._key("failures"))
            if failures >= self._threshold:
                await self._redis.set(self._key("state"), CircuitState.OPEN.value)
                await self._redis.set(self._key("opened_at"), str(time.monotonic()))
            # Auto-expire keys so stale open circuits don't block forever
            ttl = int(self._cooldown * 2)
            await self._redis.expire(self._key("state"), ttl)
            await self._redis.expire(self._key("failures"), ttl)
        except Exception:
            self._fallback.record_failure()

    async def record_success_async(self) -> None:
        """Record a success — resets the circuit to CLOSED and clears all counters."""
        try:
            await self._redis.delete(
                self._key("state"),
                self._key("failures"),
                self._key("opened_at"),
            )
        except Exception:
            self._fallback.record_success()

    # ── sync wrappers (delegate to in-memory fallback) ─────────────────────────
    # These exist so callers that cannot await (e.g. sync Celery tasks) still
    # get some protection — albeit single-replica only.

    def is_closed(self) -> bool:
        return self._fallback.is_closed()

    def can_call(self) -> bool:
        return self._fallback.can_call()

    def record_failure(self) -> None:
        self._fallback.record_failure()

    def record_success(self) -> None:
        self._fallback.record_success()

    @property
    def state(self) -> CircuitState:
        return self._fallback.state
