"""Contract tests for the canonical chat-event schema (app/chat/events.py).

This is the single source of truth for the SSE events the chat backend emits and
the frontend consumes. The tests pin the vocabulary, the wire format, and the
GoalService-event bridge mapping so the two ends can never silently drift (the
bug the old code had: the transparency panel expected event names the stream
never sent).
"""

from __future__ import annotations

import json

from app.chat.events import ChatEventType, chat_event, map_goal_event, sse, sse_event


def test_canonical_event_vocabulary_is_complete() -> None:
    # Every event the plan's §1 contract promises must exist by exact wire value.
    required = {
        "message_started",
        "token",
        "reasoning",
        "intent_classified",
        "plan_ready",
        "step_started",
        "step_complete",
        "tool_call",
        "tool_result",
        "knowledge_retrieved",
        "hitl_required",
        "hitl_resolved",
        "guardrail_blocked",
        "artifact_created",
        "schedule_created",
        "mission_started",
        "cost",
        "goal_complete",
        "error",
        "done",
    }
    values = {e.value for e in ChatEventType}
    missing = required - values
    assert not missing, f"canonical events missing from enum: {sorted(missing)}"


def test_back_compat_event_names_preserved() -> None:
    # The existing stream.py vocabulary must remain valid so nothing breaks mid-migration.
    for legacy in ("typing_started", "routing", "usage", "clarify_needed",
                   "failure_analysis", "proactive_suggestions"):
        assert legacy in {e.value for e in ChatEventType}


def test_chat_event_builder_shape() -> None:
    ev = chat_event(ChatEventType.TOKEN, token="hi", message_id="m1")
    assert ev == {"type": "token", "token": "hi", "message_id": "m1"}
    # Accepts a raw string type too (defensive) and always stringifies.
    ev2 = chat_event("step_started", step_id="s1")
    assert ev2["type"] == "step_started"


def test_sse_wire_format() -> None:
    frame = sse(chat_event(ChatEventType.DONE, session_id="s", message_id="m"))
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    body = json.loads(frame[len("data: "):].strip())
    assert body == {"type": "done", "session_id": "s", "message_id": "m"}
    # Convenience one-shot builder+formatter.
    assert sse_event(ChatEventType.TOKEN, token="x") == sse(chat_event(ChatEventType.TOKEN, token="x"))


def test_map_goal_event_bridges_real_goalservice_events() -> None:
    """GoalService/AgentGraph publish these EXACT event types to the Redis goal
    bus; the bridge maps the real vocabulary to the canonical chat vocabulary the
    frontend understands (e.g. token_chunk -> token, tool_call_complete ->
    tool_call, waiting_approval -> hitl_required, grounding_blocked ->
    guardrail_blocked, artifact_captured -> artifact_created)."""
    assert map_goal_event({"type": "token_chunk", "token": "hi"})["type"] == "token"
    assert map_goal_event({"type": "plan_ready", "steps": ["a", "b"]})["type"] == "plan_ready"
    step = map_goal_event({"type": "step_started", "step_id": "s1", "description": "d"})
    assert step["type"] == "step_started" and step["step_id"] == "s1"
    tool = map_goal_event(
        {"type": "tool_call_complete", "tool_name": "jira.search", "server": "jira"}
    )
    assert tool["type"] == "tool_call" and tool["tool"] == "jira.search"
    done = map_goal_event({"type": "goal_complete", "status": "complete"})
    assert done["type"] == "goal_complete"
    assert map_goal_event({"type": "goal_failed", "reason": "x"})["type"] == "error"
    kn = map_goal_event({"type": "knowledge_retrieved", "citations": [{"source": "x"}]})
    assert kn["type"] == "knowledge_retrieved" and kn["citations"] == [{"source": "x"}]
    assert map_goal_event({"type": "waiting_approval", "step": "deploy"})["type"] == "hitl_required"
    assert map_goal_event({"type": "hitl_approved"})["type"] == "hitl_resolved"
    assert map_goal_event({"type": "grounding_blocked"})["type"] == "guardrail_blocked"
    assert map_goal_event({"type": "artifact_captured", "artifact_id": "a1"})["type"] == (
        "artifact_created"
    )
    # unknown/internal events are dropped (return None) rather than leaking noise
    assert map_goal_event({"type": "cache_hit"}) is None
    assert map_goal_event({}) is None
