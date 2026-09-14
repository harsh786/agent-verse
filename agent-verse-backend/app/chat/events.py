"""Canonical chat-event contract — the single source of truth for chat SSE.

Both the backend stream and the frontend (`src/features/chat/chat.types.ts`) MUST
agree on this vocabulary. Historically they didn't: the transparency panel
expected AgentGraph-style names (`tool_call_complete`, …) that the chat stream
never emitted, so tool/knowledge/HITL cards silently never rendered.

This module defines:
  * ``ChatEventType`` — the exact wire values the frontend switches on.
  * ``chat_event`` / ``sse`` / ``sse_event`` — builders + SSE framing.
  * ``map_goal_event`` — the bridge that translates the REAL event vocabulary
    GoalService/AgentGraph publish to the Redis goal bus into canonical chat
    events (dropping internal/noise events).

Keep ``map_goal_event`` in lock-step with the emitters in ``app/agent/graph.py``,
``app/agent/nodes/*`` and ``app/services/goal_service.py``.
"""

from __future__ import annotations

import enum
import json
from typing import Any


class ChatEventType(enum.StrEnum):
    # Lifecycle
    MESSAGE_STARTED = "message_started"
    TYPING_STARTED = "typing_started"  # back-compat alias for message_started
    ROUTING = "routing"  # back-compat; superseded by intent_classified
    INTENT_CLASSIFIED = "intent_classified"
    DONE = "done"
    ERROR = "error"

    # Generation
    TOKEN = "token"
    REASONING = "reasoning"

    # Plan / execute / verify (from the real agent loop)
    PLAN_READY = "plan_ready"
    STEP_STARTED = "step_started"
    STEP_COMPLETE = "step_complete"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    KNOWLEDGE_RETRIEVED = "knowledge_retrieved"
    GOAL_COMPLETE = "goal_complete"

    # Governance / safety
    HITL_REQUIRED = "hitl_required"
    HITL_RESOLVED = "hitl_resolved"
    GUARDRAIL_BLOCKED = "guardrail_blocked"
    CLARIFY_NEEDED = "clarify_needed"

    # Async / platform
    ARTIFACT_CREATED = "artifact_created"
    SCHEDULE_CREATED = "schedule_created"
    MISSION_STARTED = "mission_started"

    # Telemetry
    USAGE = "usage"
    COST = "cost"

    # Diagnostics (back-compat)
    FAILURE_ANALYSIS = "failure_analysis"
    PROACTIVE_SUGGESTIONS = "proactive_suggestions"


def chat_event(event_type: ChatEventType | str, **payload: Any) -> dict[str, Any]:
    """Build a canonical event dict: ``{"type": <wire value>, **payload}``."""
    return {"type": str(event_type), **payload}


def sse(event: dict[str, Any]) -> str:
    """Frame a canonical event dict as a single SSE message."""
    return f"data: {json.dumps(event)}\n\n"


def sse_event(event_type: ChatEventType | str, **payload: Any) -> str:
    """Convenience: build + frame in one call."""
    return sse(chat_event(event_type, **payload))


# ── GoalService/AgentGraph → canonical chat-event bridge ──────────────────────
#
# The real emitters publish a richer, internal vocabulary; the chat frontend only
# understands the canonical set above. Map the meaningful ones and DROP internal
# noise (cache_hit, pattern_decision, execution_strategy_resolved, …) so the
# conversation stream stays clean.

# Events whose type name is already canonical (pass through unchanged).
_IDENTITY: frozenset[str] = frozenset(
    {
        "plan_ready",
        "step_started",
        "step_complete",
        "knowledge_retrieved",
        "goal_complete",
        "schedule_created",
        "artifact_created",
        "clarify_needed",
        "reasoning",
    }
)

# Real emitter type -> canonical chat type (rename).
_RENAME: dict[str, str] = {
    "token_chunk": "token",
    "goal_failed": "error",
    "goal_cancelled": "error",
    "goal_rejected": "error",
    "tool_call_complete": "tool_call",
    "tool_call_failed": "tool_call",
    "tool_call_pending_approval": "hitl_required",
    "waiting_approval": "hitl_required",
    "hitl_approved": "hitl_resolved",
    "hitl_rejected": "hitl_resolved",
    "approval_granted": "hitl_resolved",
    "grounding_blocked": "guardrail_blocked",
    "grounding_warning": "guardrail_blocked",
    "artifact_captured": "artifact_created",
    "goal_started": "message_started",
    "child_agent_spawned": "mission_started",
}


def map_goal_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Translate one real goal-bus event into a canonical chat event.

    Returns ``None`` for empty or internal/noise events so the caller drops them.
    Preserves the original payload (minus the old ``type``) and adds a couple of
    convenience aliases the frontend expects (e.g. ``tool`` from ``tool_name``).
    """
    raw_type = event.get("type")
    if not raw_type:
        return None

    if raw_type in _IDENTITY:
        canonical = raw_type
    elif raw_type in _RENAME:
        canonical = _RENAME[raw_type]
    else:
        return None  # internal / noise — not surfaced in chat

    payload = {k: v for k, v in event.items() if k != "type"}
    # Frontend tool cards read `tool`; AgentGraph emits `tool_name`.
    if canonical == "tool_call" and "tool" not in payload and "tool_name" in payload:
        payload["tool"] = payload["tool_name"]
    return chat_event(canonical, **payload)
