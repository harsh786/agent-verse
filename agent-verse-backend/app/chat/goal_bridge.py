"""Bridge real goal-execution events into the canonical chat SSE stream.

A chat GOAL turn submits a goal to ``GoalService`` and then streams that goal's
live events back into the conversation. ``GoalService.subscribe_events`` yields
the raw internal event dicts (local or cross-replica via Redis pub/sub); this
bridge maps each one through ``map_goal_event`` (renaming to the canonical chat
vocabulary and dropping internal noise), threads the chat identifiers onto every
frame, and closes the stream with ``done`` at the goal's terminal event.

This replaces the old simulated ``stream_goal_progress`` that emitted hardcoded
"planning → Done" steps.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

from app.chat.events import ChatEventType, map_goal_event, sse, sse_event

_TERMINAL = frozenset({ChatEventType.GOAL_COMPLETE.value, ChatEventType.ERROR.value})


async def bridge_goal_events(
    events: AsyncIterator[dict[str, Any]],
    *,
    session_id: str,
    message_id: str,
) -> AsyncGenerator[str, None]:
    """Yield canonical chat SSE frames for a goal's live event stream.

    ``events`` is any async iterator of raw goal-event dicts — in production
    ``GoalService.subscribe_events(goal_id, tenant_ctx)``; in tests a fake source.
    """
    async for raw in events:
        mapped = map_goal_event(raw)
        if mapped is None:
            continue  # internal / noise event — not surfaced in chat
        mapped.setdefault("session_id", session_id)
        mapped.setdefault("message_id", message_id)
        yield sse(mapped)
        if mapped["type"] in _TERMINAL:
            break
    yield sse_event(ChatEventType.DONE, session_id=session_id, message_id=message_id)
