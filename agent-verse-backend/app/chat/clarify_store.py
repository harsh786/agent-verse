"""Shared clarify-round counter for chat sessions.

The intent router stops asking clarifying questions after
``IntentRouter.MAX_CLARIFY_ROUNDS`` consecutive CLARIFY turns. That count used
to live in a per-process dict, so two API replicas each allowed their own three
rounds and a restart reset it. With Redis wired the count is one key per
(tenant, session) with a TTL (an idle conversation forgets its streak); the
in-memory store is only the no-Redis path (unit tests, the in-memory app).
"""

from __future__ import annotations

from typing import Any, Protocol

from app.observability.logging import get_logger

logger = get_logger(__name__)

# An abandoned clarify streak expires after a day: a user who comes back the next
# day starts fresh, and Redis never accumulates keys for dead sessions.
DEFAULT_CLARIFY_TTL_SECONDS = 24 * 60 * 60


class ClarifyRoundStore(Protocol):
    async def get(self, tenant_id: str, session_id: str) -> int: ...

    async def increment(self, tenant_id: str, session_id: str) -> int: ...

    async def reset(self, tenant_id: str, session_id: str) -> None: ...


class InMemoryClarifyRoundStore:
    """Process-local counter: the fallback when no Redis is wired."""

    def __init__(self) -> None:
        self._rounds: dict[tuple[str, str], int] = {}

    async def get(self, tenant_id: str, session_id: str) -> int:
        return self._rounds.get((tenant_id, session_id), 0)

    async def increment(self, tenant_id: str, session_id: str) -> int:
        key = (tenant_id, session_id)
        self._rounds[key] = self._rounds.get(key, 0) + 1
        return self._rounds[key]

    async def reset(self, tenant_id: str, session_id: str) -> None:
        self._rounds.pop((tenant_id, session_id), None)


class RedisClarifyRoundStore:
    """Cross-replica counter: ``INCR`` + ``EXPIRE`` on one key per session.

    A Redis error degrades to a process-local counter for that call (logged), so
    an outage never blocks a chat turn; the cap still holds within the replica.
    """

    KEY_PREFIX = "agentverse:chat:clarify"

    def __init__(self, redis: Any, *, ttl_seconds: int = DEFAULT_CLARIFY_TTL_SECONDS) -> None:
        self._redis = redis
        self._ttl = ttl_seconds
        self._degraded = InMemoryClarifyRoundStore()

    def _key(self, tenant_id: str, session_id: str) -> str:
        return f"{self.KEY_PREFIX}:{tenant_id}:{session_id}"

    async def get(self, tenant_id: str, session_id: str) -> int:
        try:
            raw = await self._redis.get(self._key(tenant_id, session_id))
        except Exception as exc:
            logger.warning("chat_clarify_store_degraded", op="get", error=str(exc))
            return await self._degraded.get(tenant_id, session_id)
        if raw is None:
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    async def increment(self, tenant_id: str, session_id: str) -> int:
        key = self._key(tenant_id, session_id)
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.incr(key)
            pipe.expire(key, self._ttl)
            value, _ = await pipe.execute()
            return int(value)
        except Exception as exc:
            logger.warning("chat_clarify_store_degraded", op="increment", error=str(exc))
            return await self._degraded.increment(tenant_id, session_id)

    async def reset(self, tenant_id: str, session_id: str) -> None:
        await self._degraded.reset(tenant_id, session_id)
        try:
            await self._redis.delete(self._key(tenant_id, session_id))
        except Exception as exc:
            logger.warning("chat_clarify_store_degraded", op="reset", error=str(exc))
