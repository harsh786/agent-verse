"""SSE stream generator for the chat feature.

Multiplexes two sources:
1. LLM token generator — for QA streaming
2. Redis pub/sub goal events — for GOAL execution progress

Events emitted (all prefixed ``data: <json>\n\n``):
  typing_started, routing, token, step_started, step_complete, tool_call,
  clarify_needed, hitl_required, failure_analysis, proactive_suggestions,
  reasoning, artifact_created, schedule_created, done, error
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any


def _sse(event_type: str, payload: dict[str, Any]) -> str:
    """Format a single SSE message."""
    data = json.dumps({"type": event_type, **payload})
    return f"data: {data}\n\n"


async def stream_qa_response(
    session_id: str,
    message_id: str,
    content_tokens: list[str],
    usage: dict[str, Any] | None = None,
    reasoning_tokens: list[str] | None = None,
    show_reasoning: bool = False,
) -> AsyncGenerator[str, None]:
    """Simulate QA streaming — yields SSE events for each token.

    In production this is driven by a real LLM streaming response.
    The caller provides pre-generated tokens (or real async token iterator).
    """
    yield _sse("typing_started", {"session_id": session_id, "message_id": message_id})

    yield _sse("routing", {"session_id": session_id, "intent": "QA"})

    # Reasoning tokens (thinking mode)
    if show_reasoning and reasoning_tokens:
        for tok in reasoning_tokens:
            yield _sse("reasoning", {"token": tok, "message_id": message_id})

    # Content tokens
    for tok in content_tokens:
        yield _sse("token", {"token": tok, "message_id": message_id})

    # Final usage info
    if usage:
        yield _sse(
            "usage",
            {
                "message_id": message_id,
                "tokens_in": usage.get("tokens_in", 0),
                "tokens_out": usage.get("tokens_out", 0),
                "cost_usd": usage.get("cost_usd", 0.0),
                "model": usage.get("model", ""),
            },
        )

    yield _sse("done", {"session_id": session_id, "message_id": message_id})


async def stream_goal_progress(
    session_id: str,
    message_id: str,
    goal_id: str,
    steps: list[dict[str, Any]],
    outcome: str = "complete",
    failure_reason: str | None = None,
    suggestions: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    """Simulate GOAL streaming — yields step progress events.

    In production this bridges real Redis pub/sub events from GoalService.
    """
    yield _sse("typing_started", {"session_id": session_id, "message_id": message_id})
    yield _sse("routing", {"session_id": session_id, "intent": "GOAL", "goal_id": goal_id})

    for step in steps:
        yield _sse("step_started", {"goal_id": goal_id, "step": step.get("name", ""), **step})

        # Tool calls within step
        for tool in step.get("tool_calls", []):
            yield _sse("tool_call", {
                "goal_id": goal_id,
                "step": step.get("name", ""),
                "tool": tool,
            })

        yield _sse("step_complete", {
            "goal_id": goal_id,
            "step": step.get("name", ""),
            "result": step.get("result", ""),
        })

    if outcome == "failure" and failure_reason:
        yield _sse(
            "failure_analysis",
            {
                "goal_id": goal_id,
                "reason": failure_reason,
                "suggestions": suggestions or [],
            },
        )
    else:
        if suggestions:
            yield _sse("proactive_suggestions", {"goal_id": goal_id, "suggestions": suggestions})

    yield _sse("done", {"session_id": session_id, "message_id": message_id, "goal_id": goal_id})


async def stream_clarify(
    session_id: str,
    message_id: str,
    question: str,
    options: list[str],
    round: int = 1,  # noqa: A002
) -> AsyncGenerator[str, None]:
    """Emit a clarify_needed event."""
    yield _sse("typing_started", {"session_id": session_id, "message_id": message_id})
    yield _sse("routing", {"session_id": session_id, "intent": "CLARIFY"})
    yield _sse(
        "clarify_needed",
        {
            "session_id": session_id,
            "message_id": message_id,
            "question": question,
            "options": options,
            "round": round,
        },
    )
    yield _sse("done", {"session_id": session_id, "message_id": message_id})


async def stream_hitl(
    session_id: str,
    message_id: str,
    goal_id: str,
    step_name: str,
    risk_level: str = "high",
    timeout_seconds: int = 300,
) -> AsyncGenerator[str, None]:
    """Emit a hitl_required event — pauses goal execution until approved."""
    yield _sse(
        "hitl_required",
        {
            "session_id": session_id,
            "message_id": message_id,
            "goal_id": goal_id,
            "step": step_name,
            "risk_level": risk_level,
            "timeout_seconds": timeout_seconds,
            "approval_token": uuid.uuid4().hex,
        },
    )
    yield _sse("done", {"session_id": session_id, "message_id": message_id})


async def stream_schedule_created(
    session_id: str,
    message_id: str,
    cron: str,
    human_schedule: str,
    next_run: str | None = None,
) -> AsyncGenerator[str, None]:
    """Emit a schedule_created confirmation event."""
    yield _sse(
        "schedule_created",
        {
            "session_id": session_id,
            "message_id": message_id,
            "cron_expression": cron,
            "human_schedule": human_schedule,
            "next_run": next_run,
        },
    )
    yield _sse("done", {"session_id": session_id, "message_id": message_id})


async def stream_artifact_created(
    session_id: str,
    message_id: str,
    artifact_id: str,
    title: str,
    language: str,
) -> AsyncGenerator[str, None]:
    """Emit an artifact_created event."""
    yield _sse(
        "artifact_created",
        {
            "session_id": session_id,
            "message_id": message_id,
            "artifact_id": artifact_id,
            "title": title,
            "language": language,
        },
    )
