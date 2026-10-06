"""Redis-backed circuit breaker — state shared across all worker replicas.

Each tool per tenant has its own set of circuit breaker keys in Redis:
  cb:{tenant_id}:{tool_name}:state    → "closed" | "open" | "half_open"
  cb:{tenant_id}:{tool_name}:failures → integer count
  cb:{tenant_id}:{tool_name}:opened_at → epoch float (wall-clock, time.time())

A TTL of 2x the cooldown period is applied so stale keys self-expire.

Falls back to the in-memory :class:`~app.reliability.circuit_breaker.CircuitBreaker`
if Redis is unavailable, so a Redis outage never takes down the whole service.

H16 fix: ``opened_at`` now stores ``time.time()`` (wall-clock UTC epoch) rather
than ``time.monotonic()``.  Monotonic clocks are per-process and cannot be
meaningfully compared across replicas; wall-clock epoch timestamps are shared
across all processes on all hosts and therefore give correct cross-replica
elapsed-time calculations.
"""

from __future__ import annotations

import time
from typing import Any

from app.reliability.circuit_breaker import CircuitBreaker, CircuitState


class RedisCircuitBreaker:
    """Circuit breaker backed by Redis for cross-replica state sharing.

    Args:
        redis_client: An ``redis.asyncio.Redis``-compatible async client.
                      *Alias*: ``redis`` (convenience param, lower priority).
        tenant_id:    Tenant owning this breaker.
        tool_name:    Tool (or service) this breaker guards.
        key:          Override the full Redis key prefix (e.g. for tests).
                      When supplied, ``tenant_id`` / ``tool_name`` are ignored
                      for key construction.
        failure_threshold: Consecutive failures required to open the circuit.
        cooldown_seconds:  Seconds to wait before allowing a half-open probe.
                           *Alias*: ``reset_timeout``.
    """

    def __init__(
        self,
        *,
        redis_client: Any = None,
        redis: Any = None,
        tenant_id: str = "",
        tool_name: str = "",
        key: str = "",
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        reset_timeout: float = 0.0,
    ) -> None:
        # Accept either redis_client= (canonical) or redis= (alias)
        self._redis = redis_client if redis_client is not None else redis
        self._tenant_id = tenant_id
        self._tool_name = tool_name
        self._threshold = failure_threshold
        # reset_timeout= is an alias for cooldown_seconds= (test-friendly name)
        self._cooldown = reset_timeout if reset_timeout else cooldown_seconds
        # key= overrides derived prefix (useful in tests)
        self._prefix = key if key else f"cb:{tenant_id}:{tool_name}"
        # In-memory fallback used when Redis is unreachable
        self._fallback = CircuitBreaker(
            failure_threshold=failure_threshold,
            cooldown_seconds=self._cooldown,
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
        Uses ``time.time()`` (wall-clock) to compare against the stored
        ``opened_at`` timestamp so the comparison is valid across replicas.

        HALF_OPEN is meant to let exactly *one* probe through across the
        whole fleet before the downstream's health is re-established. The
        state alone can't enforce that: once any replica flips
        ``state`` to ``half_open``, a plain ``state == HALF_OPEN`` check lets
        every other replica's concurrent caller through too — e.g. a goal's
        parallel tool-call wave hitting the same cooling-down connector would
        send its whole burst at once the instant the cooldown expires,
        instead of one probe. A ``SET ... NX`` on a dedicated claim key is
        atomic in Redis, so only the caller that wins it proceeds; everyone
        else is blocked until ``record_success_async``/``record_failure_async``
        resolves the probe (or the claim's TTL expires if the prober died
        without reporting either way).
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
                    # H16: use wall-clock (time.time()) for cross-replica correctness
                    if time.time() - opened_at >= self._cooldown:
                        claimed = await self._claim_half_open_probe()
                        if claimed:
                            # With a TTL: a plain SET dropped the TTL that
                            # record_failure put on the key, so a HALF_OPEN
                            # whose probe never reported stayed forever.
                            await self._redis.set(
                                self._key("state"),
                                CircuitState.HALF_OPEN.value,
                                ex=self._state_ttl(),
                            )
                        return claimed
                return False

            if state == CircuitState.HALF_OPEN:
                # A probe is in flight somewhere in the fleet while its claim
                # key lives; everyone else is blocked until it reports. A
                # prober that died without reporting lets its claim expire:
                # the next caller claims a fresh probe (a08-F198-02 — this
                # branch used to refuse forever, wedging the breaker
                # HALF_OPEN fleet-wide).
                return await self._claim_half_open_probe()

            return False
        except Exception:
            return self._fallback.can_call()

    def _state_ttl(self) -> int:
        return max(1, int(self._cooldown * 2))

    async def _claim_half_open_probe(self) -> bool:
        """Atomically claim the single HALF_OPEN probe slot.

        Returns True only for the one caller (across all replicas) that wins
        the ``SET NX`` race. The claim has its own short TTL so a prober that
        crashes mid-call doesn't wedge the breaker open forever.
        """
        claim_ttl = max(1, int(min(self._cooldown, 30)))
        claimed = await self._redis.set(
            self._key("half_open_claim"), "1", nx=True, ex=claim_ttl
        )
        return bool(claimed)

    async def record_failure_async(self) -> None:
        """Record a failure.  Opens the circuit once ``failure_threshold`` is reached."""
        try:
            failures = await self._redis.incr(self._key("failures"))
            if failures >= self._threshold:
                await self._redis.set(self._key("state"), CircuitState.OPEN.value)
                # H16: store wall-clock epoch so cross-replica elapsed-time math works
                ttl = int(self._cooldown * 2)
                await self._redis.set(
                    self._key("opened_at"),
                    str(time.time()),
                    ex=ttl,  # auto-expire so stale open-state keys are cleaned up
                )
            # Auto-expire keys so stale open circuits don't block forever
            ttl = int(self._cooldown * 2)
            await self._redis.expire(self._key("state"), ttl)
            await self._redis.expire(self._key("failures"), ttl)
            # Release the half-open probe claim (if this failure was the
            # probe's result) so the *next* cooldown expiry can claim a
            # fresh probe instead of waiting out the claim's own TTL.
            await self._redis.delete(self._key("half_open_claim"))
        except Exception:
            self._fallback.record_failure()

    async def record_success_async(self) -> None:
        """Record a success — resets the circuit to CLOSED and clears all counters."""
        try:
            await self._redis.delete(
                self._key("state"),
                self._key("failures"),
                self._key("opened_at"),
                self._key("half_open_claim"),
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


# Thresholds of the per-tenant LLM-provider breaker every goal graph gets.
LLM_BREAKER_FAILURE_THRESHOLD = 3
LLM_BREAKER_COOLDOWN_S = 120.0


def build_llm_circuit_breaker(redis: Any, tenant_id: str) -> RedisCircuitBreaker | CircuitBreaker:
    """The tenant's LLM-provider breaker for one goal graph (API and worker alike).

    With a Redis client the state is shared by every replica and worker
    (``cb:{tenant}:llm_provider:*``); without one it is process-local. The
    worker graph had no breaker at all (a08-F198-03).
    """
    if redis is not None:
        return RedisCircuitBreaker(
            redis_client=redis,
            tenant_id=tenant_id,
            tool_name="llm_provider",
            failure_threshold=LLM_BREAKER_FAILURE_THRESHOLD,
            cooldown_seconds=LLM_BREAKER_COOLDOWN_S,
        )
    return CircuitBreaker(
        failure_threshold=LLM_BREAKER_FAILURE_THRESHOLD,
        cooldown_seconds=LLM_BREAKER_COOLDOWN_S,
    )
