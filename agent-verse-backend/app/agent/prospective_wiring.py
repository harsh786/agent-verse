"""Surface due/upcoming prospective intentions into the planner context.

The ``ProspectiveMemoryService`` (already created on ``app.state`` in the app
lifespan) holds deferred intentions ("follow up on X next week"). This renders a
bounded ``[Pending intentions]`` block, due-first, so a planning pass addresses
what the agent previously deferred. Read-only — surfacing must not lease/claim
items for execution (that is the scheduler's job via ``lease_due``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any


def pending_intentions_block(
    items: Sequence[Any],
    now: datetime,
    *,
    max_items: int = 5,
    max_chars: int = 400,
) -> str:
    """Render due-first pending intentions as a compact planner block.

    Empty string when there is nothing to surface (caller omits the block).
    """
    due = [it for it in items if getattr(it, "due_at", now) <= now]
    upcoming = [it for it in items if getattr(it, "due_at", now) > now]
    chosen = (due + upcoming)[:max_items]
    if not chosen:
        return ""
    lines: list[str] = []
    total = 0
    for item in chosen:
        due_at = getattr(item, "due_at", now)
        when = "DUE NOW" if due_at <= now else due_at.isoformat()
        intention = str(getattr(item, "intention", ""))[:160]
        line = f"- ({when}) {intention}"
        total += len(line)
        if total > max_chars:
            break
        lines.append(line)
    return "\n".join(lines)
