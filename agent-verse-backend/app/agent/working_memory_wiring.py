"""Wire the run-scoped working memory into the executor context.

Working memory is volatile and lives in the checkpointed ``AgentState.context``
as a plain, serializable list of dicts (NOT the durable canonical memory store).
These helpers keep it in sync with completed steps and render a bounded,
salience-ranked ``[Working memory]`` block for the executor prompt — so the
executor sees the most *relevant* prior observations across the whole run, not
merely the last three (which ``Recent outputs`` already covers).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.memory.salience import SalienceScorer
from app.memory.working_memory import WorkingMemory

_WM_KEY = "_working_memory"
_WM_IDS_KEY = "_working_memory_ids"
_DEFAULT_CAPACITY = 12


def sync_working_memory(
    context: dict[str, Any],
    steps: Sequence[Any],
    *,
    capacity: int = _DEFAULT_CAPACITY,
) -> None:
    """Record any completed step outputs not yet in working memory (idempotent).

    Tracks recorded steps by ``step_id`` so repeated calls across the loop don't
    duplicate entries, and trims the buffer to ``capacity`` (oldest first).
    """
    recorded: list[str] = context.setdefault(_WM_IDS_KEY, [])
    store: list[dict[str, str]] = context.setdefault(_WM_KEY, [])
    seen = set(recorded)
    for step in steps:
        step_id = str(getattr(step, "step_id", "") or "")
        output = (getattr(step, "output", "") or "").strip()
        if not output or (step_id and step_id in seen):
            continue
        source = (getattr(step, "description", "") or "step")[:60]
        store.append({"content": output, "source": source, "step_id": step_id})
        if step_id:
            recorded.append(step_id)
            seen.add(step_id)
    if len(store) > capacity:
        del store[: len(store) - capacity]


def working_memory_block(
    context: dict[str, Any],
    *,
    focus: str,
    max_chars: int = 400,
    capacity: int = _DEFAULT_CAPACITY,
) -> str:
    """Render a salience-ranked working-memory block for the executor prompt.

    Empty string when nothing is recorded (caller omits the block entirely).
    """
    items: list[dict[str, str]] = context.get(_WM_KEY, [])
    if not items:
        return ""
    wm = WorkingMemory(
        capacity=max(capacity, len(items)),
        scorer=SalienceScorer(),
        focus=focus,
    )
    for item in items:
        wm.push(item.get("content", ""), source=item.get("source", ""))
    return wm.format_for_prompt(max_chars=max_chars, salient=True)
