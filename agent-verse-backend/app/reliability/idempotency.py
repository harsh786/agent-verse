"""Redis-backed idempotency store for goal submissions."""

from __future__ import annotations

import json
from typing import Any

# A claim that is still being processed. Short-lived on purpose: if the replica
# holding it dies mid-request the key frees itself instead of blocking retries
# for the full replay window.
_PENDING_TTL_SECONDS = 120


class IdempotencyStore:
    """Prevents duplicate goal submissions using Redis SET NX with TTL.

    Keyed by caller-supplied idempotency key, scoped per tenant.

    Lifecycle (``claim`` → ``complete`` | ``release``):

    * ``claim`` atomically takes the key in a *pending* state, or reports what
      is already there — ``{"state": "pending"}`` for a request still in
      flight, ``{"state": "done", "response": {...}}`` for one that finished.
    * ``complete`` stores the finished response (with the created goal_id) so a
      replay can return it instead of a bare 409.
    * ``release`` drops the claim when the submission failed, so the client can
      retry with the same key.
    """

    KEY_PREFIX = "idempotency:"

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    def _key(self, key: str, tenant_id: str) -> str:
        return f"{self.KEY_PREFIX}{tenant_id}:{key}"

    async def check_and_set(self, key: str, tenant_id: str, ttl_seconds: int = 3600) -> bool:
        """Return True if key is new (should process), False if duplicate."""
        result = await self._redis.set(self._key(key, tenant_id), "1", nx=True, ex=ttl_seconds)
        return result is not None  # None → key already exists → duplicate

    async def claim(
        self, key: str, tenant_id: str, *, pending_ttl_seconds: int = _PENDING_TTL_SECONDS
    ) -> dict[str, Any] | None:
        """Claim *key*; ``None`` when this caller now owns it, else the existing entry.

        Redis errors propagate: the caller must fail closed rather than run the
        submission without duplicate protection.
        """
        redis_key = self._key(key, tenant_id)
        pending = json.dumps({"state": "pending"})
        if await self._redis.set(redis_key, pending, nx=True, ex=pending_ttl_seconds):
            return None
        raw = await self._redis.get(redis_key)
        if raw is None:
            # Expired between SET NX and GET — treat as in flight; a retry claims it.
            return {"state": "pending"}
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        try:
            entry = json.loads(text)
        except ValueError:
            entry = None
        if not isinstance(entry, dict) or entry.get("state") not in {"pending", "done"}:
            # A legacy check_and_set marker ("1"): a request was received, result unknown.
            return {"state": "pending"}
        return entry

    async def complete(
        self, key: str, tenant_id: str, response: dict[str, Any], ttl_seconds: int = 3600
    ) -> None:
        """Record the finished submission's response for replays."""
        await self._redis.set(
            self._key(key, tenant_id),
            json.dumps({"state": "done", "response": response}, default=str),
            ex=ttl_seconds,
        )

    async def release(self, key: str, tenant_id: str) -> None:
        """Release an idempotency key (e.g., if the request failed and should be retried)."""
        await self._redis.delete(self._key(key, tenant_id))

    async def exists(self, key: str, tenant_id: str) -> bool:
        """Check if key exists without setting it."""
        return bool(await self._redis.exists(self._key(key, tenant_id)))
