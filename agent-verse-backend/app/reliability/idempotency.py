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

    def _pending_value(self, owner: str, body_hash: str) -> str:
        return json.dumps({"state": "pending", "owner": owner, "body": body_hash}, sort_keys=True)

    async def claim(
        self,
        key: str,
        tenant_id: str,
        *,
        owner: str = "",
        body_hash: str = "",
        pending_ttl_seconds: float | None = None,
    ) -> dict[str, Any] | None:
        """Claim *key*; ``None`` when this caller now owns it, else the existing entry.

        The pending value records the claiming request's *owner* token (so only
        it can complete, extend or release the claim) and *body_hash* (so a
        reuse of the key for a different request is detectable: the existing
        entry's ``body`` is returned to the caller).

        Redis errors propagate: the caller must fail closed rather than run the
        submission without duplicate protection.
        """
        ttl = _PENDING_TTL_SECONDS if pending_ttl_seconds is None else pending_ttl_seconds
        redis_key = self._key(key, tenant_id)
        pending = self._pending_value(owner, body_hash)
        if await self._redis.set(redis_key, pending, nx=True, px=max(1, int(ttl * 1000))):
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
        entry.pop("owner", None)
        return entry

    async def _compare_and_apply(self, redis_key: str, expected: str | None, apply: Any) -> bool:
        """Run *apply(pipe)* in MULTI only while the key's value is *expected*
        (``None``: the key is absent). WATCH makes the check-and-write atomic."""
        from redis.exceptions import WatchError

        async with self._redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(redis_key)
                current = await pipe.get(redis_key)
                if isinstance(current, bytes):
                    current = current.decode()
                if current != expected:
                    await pipe.unwatch()
                    return False
                pipe.multi()
                apply(pipe)
                await pipe.execute()
                return True
            except WatchError:
                return False

    async def complete(
        self,
        key: str,
        tenant_id: str,
        response: dict[str, Any],
        ttl_seconds: int = 3600,
        *,
        owner: str | None = None,
        body_hash: str = "",
    ) -> bool:
        """Record the finished submission's response for replays.

        With *owner*, only while this owner's pending claim is still there (or
        the claim lapsed and nobody re-claimed): a stale request never
        overwrites a newer claim. Returns whether the response was recorded.
        """
        redis_key = self._key(key, tenant_id)
        done = json.dumps({"state": "done", "response": response, "body": body_hash}, default=str)
        if owner is None:
            await self._redis.set(redis_key, done, ex=ttl_seconds)
            return True

        def _write(pipe: Any) -> None:
            pipe.set(redis_key, done, ex=ttl_seconds)

        pending = self._pending_value(owner, body_hash)
        if await self._compare_and_apply(redis_key, pending, _write):
            return True
        return await self._compare_and_apply(redis_key, None, _write)

    async def extend(
        self, key: str, tenant_id: str, *, owner: str, body_hash: str, ttl_seconds: float
    ) -> bool:
        """Heartbeat: push this owner's pending claim's expiry out by *ttl_seconds*."""
        redis_key = self._key(key, tenant_id)

        def _expire(pipe: Any) -> None:
            pipe.pexpire(redis_key, max(1, int(ttl_seconds * 1000)))

        return await self._compare_and_apply(
            redis_key, self._pending_value(owner, body_hash), _expire
        )

    async def release(
        self, key: str, tenant_id: str, *, owner: str | None = None, body_hash: str = ""
    ) -> None:
        """Release an idempotency key (e.g., if the request failed and should be retried).

        With *owner*, only this owner's pending claim is deleted.
        """
        redis_key = self._key(key, tenant_id)
        if owner is None:
            await self._redis.delete(redis_key)
            return
        await self._compare_and_apply(
            redis_key, self._pending_value(owner, body_hash), lambda pipe: pipe.delete(redis_key)
        )

    async def exists(self, key: str, tenant_id: str) -> bool:
        """Check if key exists without setting it."""
        return bool(await self._redis.exists(self._key(key, tenant_id)))
