"""Phase 0.2 — the chat↔goal SSE bridge.

Wraps the raw event stream from ``GoalService.subscribe_events`` and turns it into
canonical chat SSE frames the frontend understands, dropping internal noise and
terminating on the goal's terminal event.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.chat.goal_bridge import bridge_goal_events


async def _fake_goal_stream(events: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for e in events:
        yield e


def _parse(frames: list[str]) -> list[dict[str, Any]]:
    out = []
    for f in frames:
        assert f.startswith("data: ") and f.endswith("\n\n")
        out.append(json.loads(f[len("data: "):].strip()))
    return out


async def _collect(gen: AsyncIterator[str]) -> list[str]:
    return [f async for f in gen]


async def test_bridge_maps_real_events_drops_noise_and_terminates() -> None:
    raw = [
        {"type": "goal_started", "goal_id": "g1"},
        {"type": "cache_hit", "detail": "noise"},  # internal — must be dropped
        {"type": "plan_ready", "steps": ["search", "post"]},
        {"type": "token_chunk", "token": "Found "},
        {"type": "tool_call_complete", "tool_name": "jira.search", "server": "jira"},
        {"type": "pattern_decision", "x": 1},  # internal — dropped
        {"type": "goal_complete", "status": "complete"},
        {"type": "token_chunk", "token": "AFTER — must not appear"},  # after terminal
    ]
    frames = await _collect(
        bridge_goal_events(_fake_goal_stream(raw), session_id="s1", message_id="m1")
    )
    events = _parse(frames)
    types = [e["type"] for e in events]

    # Opens with message_started, closes with done.
    assert types[0] == "message_started"
    assert types[-1] == "done"
    # Noise dropped; real events mapped to canonical names, in order.
    assert "cache_hit" not in types and "pattern_decision" not in types
    assert types == [
        "message_started",  # goal_started -> message_started
        "plan_ready",
        "token",  # token_chunk -> token
        "tool_call",  # tool_call_complete -> tool_call
        "goal_complete",
        "done",
    ]
    # Stops at the terminal event — nothing after goal_complete leaks in.
    assert "AFTER — must not appear" not in json.dumps(events)
    # session/message ids threaded onto every frame.
    assert all(e.get("session_id") == "s1" and e.get("message_id") == "m1" for e in events)
    # tool alias present.
    tool = next(e for e in events if e["type"] == "tool_call")
    assert tool["tool"] == "jira.search"


async def test_bridge_terminates_on_error() -> None:
    raw = [
        {"type": "plan_ready", "steps": ["x"]},
        {"type": "goal_failed", "reason": "boom"},
        {"type": "token_chunk", "token": "should not appear"},
    ]
    events = _parse(
        await _collect(bridge_goal_events(_fake_goal_stream(raw), session_id="s", message_id="m"))
    )
    types = [e["type"] for e in events]
    assert "error" in types and types[-1] == "done"
    assert "should not appear" not in json.dumps(events)
