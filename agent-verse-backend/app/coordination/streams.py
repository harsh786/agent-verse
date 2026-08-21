"""Bounded Redis Streams transport for coordination event envelopes."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol


class BackpressureError(RuntimeError):
    """The bounded stream cannot safely accept more entries."""


class RedisStreamsClient(Protocol):
    async def xlen(self, stream: str) -> int: ...

    async def xadd(self, stream: str, fields: dict[str, str], **kwargs: Any) -> str: ...

    async def xack(self, stream: str, group: str, message_id: str) -> int: ...


class CoordinationStreams:
    """Publish and acknowledge unchanged envelopes using service-role groups."""

    _token = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
    _roles = frozenset({"executor", "projector", "notifier", "recovery", "outbox"})

    def __init__(self, redis: RedisStreamsClient, *, max_stream_length: int = 10_000) -> None:
        if max_stream_length <= 0:
            raise ValueError("max_stream_length must be positive")
        self._redis = redis
        self._max_stream_length = max_stream_length

    @classmethod
    def stream_name(cls, tenant_id: str, session_id: str) -> str:
        if not cls._token.fullmatch(tenant_id) or not cls._token.fullmatch(session_id):
            raise ValueError("invalid tenant or session stream token")
        return f"coord:{tenant_id}:{session_id}"

    async def publish(self, tenant_id: str, session_id: str, envelope: dict[str, Any]) -> str:
        stream = self.stream_name(tenant_id, session_id)
        if await self._redis.xlen(stream) >= self._max_stream_length:
            raise BackpressureError(f"coordination stream is saturated: {stream}")
        fields = {
            "event_id": str(envelope.get("event_id", "")),
            "envelope": json.dumps(
                envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ),
        }
        return await self._redis.xadd(stream, fields)

    async def ack(
        self,
        tenant_id: str,
        session_id: str,
        *,
        role: str,
        message_id: str,
    ) -> int:
        if role not in self._roles:
            raise ValueError("unknown bounded consumer role")
        return await self._redis.xack(
            self.stream_name(tenant_id, session_id),
            f"coord-{role}",
            message_id,
        )

    @staticmethod
    def decode(fields: dict[str, str | bytes]) -> dict[str, Any]:
        raw = fields["envelope"]
        if isinstance(raw, bytes):
            raw = raw.decode()
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("stream envelope must be an object")
        return decoded


__all__ = ["BackpressureError", "CoordinationStreams", "RedisStreamsClient"]
