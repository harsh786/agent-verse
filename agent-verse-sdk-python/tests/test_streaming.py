"""Tests for SSE streaming client."""
from __future__ import annotations

import httpx
import pytest
import respx

from agentverse.streaming import stream_coordination_sse, stream_sse

BASE_URL = "http://localhost:8000"
HEADERS = {"X-API-Key": "test-key"}


async def test_stream_sse_yields_events():
    sse_body = (
        'data: {"type": "goal_started", "goal_id": "g1", "goal": "Test"}\n\n'
        'data: {"type": "goal_complete", "goal_id": "g1"}\n\n'
        "data: [DONE]\n\n"
    )
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/goals/g1/stream").mock(
            return_value=httpx.Response(200, text=sse_body)
        )
        events = []
        async for evt in stream_sse(f"{BASE_URL}/goals/g1/stream", HEADERS):
            events.append(evt)
    assert len(events) == 2
    assert events[0].type == "goal_started"
    assert events[1].type == "goal_complete"


async def test_stream_sse_skips_malformed_json():
    sse_body = (
        "data: not-json\n\n"
        'data: {"type": "ok", "goal_id": "g2"}\n\n'
        "data: [DONE]\n\n"
    )
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{BASE_URL}/goals/g2/stream").mock(
            return_value=httpx.Response(200, text=sse_body)
        )
        events = []
        async for evt in stream_sse(f"{BASE_URL}/goals/g2/stream", HEADERS):
            events.append(evt)
    assert len(events) == 1
    assert events[0].type == "ok"


async def test_coordination_stream_resumes_and_deduplicates():
    event = '{"event_id":"e1","session_id":"s1","sequence":8,"event_type":"message","schema_version":1}'
    body = f"id: 8\ndata: {event}\n\nid: 8\ndata: {event}\n\n"
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{BASE_URL}/coordination/s1").mock(
            return_value=httpx.Response(200, text=body)
        )
        events = [
            item
            async for item in stream_coordination_sse(
                f"{BASE_URL}/coordination/s1", HEADERS, after_sequence=7
            )
        ]
    assert [item.sequence for item in events] == [8]
    assert route.calls.last.request.headers["Last-Event-ID"] == "7"


async def test_coordination_stream_rejects_malformed_frames():
    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{BASE_URL}/coordination/s1").mock(
            return_value=httpx.Response(200, text="data: not-json\n\n")
        )
        with pytest.raises(ValueError, match="malformed"):
            async for _ in stream_coordination_sse(
                f"{BASE_URL}/coordination/s1", HEADERS
            ):
                pass
