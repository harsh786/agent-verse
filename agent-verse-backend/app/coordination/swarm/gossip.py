"""Deduplicating, credentialed, TTL-limited gossip router."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime

from app.coordination.swarm.models import GossipMessage


class GossipRouter:
    def __init__(self, *, max_messages_per_origin: int) -> None:
        if max_messages_per_origin <= 0:
            raise ValueError("origin limit must be positive")
        self._limit = max_messages_per_origin
        self._seen: set[tuple[str, str]] = set()
        self._counts: Counter[tuple[str, str]] = Counter()

    def accept(
        self,
        message: GossipMessage,
        *,
        credential_validator: Callable[[str, str], bool],
        now: datetime | None = None,
    ) -> GossipMessage | None:
        current = now or datetime.now(UTC)
        if message.expires_at <= current or message.hops_remaining <= 0:
            return None
        if not credential_validator(message.origin_agent_id, message.origin_credential):
            raise PermissionError("forged swarm origin")
        identity = (message.tenant_id, message.event_id)
        if identity in self._seen:
            return None
        origin = (message.tenant_id, message.origin_agent_id)
        if self._counts[origin] >= self._limit:
            return None
        self._seen.add(identity)
        self._counts[origin] += 1
        return message.model_copy(update={"hops_remaining": message.hops_remaining - 1})


__all__ = ["GossipRouter"]
