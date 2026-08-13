from __future__ import annotations

import pytest

from app.coordination.streams import BackpressureError, CoordinationStreams


class Redis:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, str]]] = []
        self.acks: list[tuple[str, str, str]] = []

    async def xlen(self, _: str) -> int:
        return len(self.entries)

    async def xadd(self, stream: str, fields: dict[str, str], **_: object) -> str:
        self.entries.append((stream, fields))
        return f"{len(self.entries)}-0"

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acks.append((stream, group, message_id))
        return 1


async def test_publish_uses_tenant_session_stream_and_unchanged_envelope() -> None:
    redis = Redis()
    streams = CoordinationStreams(redis, max_stream_length=10)
    envelope = {"event_id": "e1", "sequence": 1, "payload": {"safe": True}}

    message_id = await streams.publish("tenant-1", "session-1", envelope)

    assert message_id == "1-0"
    assert redis.entries[0][0] == "coord:tenant-1:session-1"
    assert streams.decode(redis.entries[0][1]) == envelope


async def test_publish_applies_backpressure_before_xadd() -> None:
    redis = Redis()
    redis.entries.append(("existing", {}))
    streams = CoordinationStreams(redis, max_stream_length=1)

    with pytest.raises(BackpressureError):
        await streams.publish("tenant", "session", {"event_id": "e1"})


async def test_ack_uses_bounded_role_group_name() -> None:
    redis = Redis()
    streams = CoordinationStreams(redis, max_stream_length=10)

    await streams.ack("tenant", "session", role="executor", message_id="1-0")

    assert redis.acks == [("coord:tenant:session", "coord-executor", "1-0")]
    with pytest.raises(ValueError, match="role"):
        await streams.ack("tenant", "session", role="process-123", message_id="1-0")
