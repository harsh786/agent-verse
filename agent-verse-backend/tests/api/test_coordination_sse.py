from __future__ import annotations

import json

from app.coordination.replay import ReplayEvent
from app.coordination.streaming import encode_heartbeat, encode_sse_event


def test_sse_envelope_is_versioned_replayable_and_redacted() -> None:
    encoded = encode_sse_event(
        ReplayEvent(
            tenant_id="tenant-1",
            session_id="session-1",
            sequence=7,
            event_id="event-7",
            schema_version=1,
            event_type="handoff.accepted.v1",
            payload={"result": "ok", "private_reasoning": "hidden"},
        )
    )
    data_line = next(line for line in encoded.splitlines() if line.startswith("data: "))
    data = json.loads(data_line.removeprefix("data: "))

    assert encoded.startswith("id: 7\nevent: handoff.accepted.v1\n")
    assert data["schema_version"] == 1
    assert data["session_id"] == data["run_id"] == "session-1"
    assert data["producer"] == "coordination-runtime"
    assert data["payload"]["private_reasoning"] == "[REDACTED]"


def test_sse_heartbeat_is_comment_frame() -> None:
    assert encode_heartbeat() == ": heartbeat\n\n"
