"""Working memory — bounded short-term context window for the active agent run.

Analogous to human working memory: a small, fast, volatile store that holds
the most recently observed facts/tool outputs during a single goal execution.
Items are evicted (oldest first) when capacity is exceeded.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Iterator


@dataclass
class WorkingMemoryItem:
    content: str
    source: str = ""  # e.g. "tool_output", "rag_chunk", "observation"
    metadata: dict[str, Any] = field(default_factory=dict)
    added_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class WorkingMemory:
    """A bounded FIFO queue of recent context items for the active goal.

    Parameters
    ----------
    capacity : int
        Maximum number of items stored. When exceeded, the oldest item is
        evicted automatically.
    """

    def __init__(self, capacity: int = 10) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._items: deque[WorkingMemoryItem] = deque(maxlen=capacity)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def push(
        self,
        content: str,
        *,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> WorkingMemoryItem:
        """Add an item, evicting the oldest if capacity is exceeded."""
        item = WorkingMemoryItem(
            content=content,
            source=source,
            metadata=metadata or {},
        )
        self._items.append(item)
        return item

    def snapshot(self) -> list[WorkingMemoryItem]:
        """Return a copy of current items, oldest first."""
        return list(self._items)

    def snapshot_as_dicts(self) -> list[dict[str, Any]]:
        """Return items as plain dicts suitable for prompt injection."""
        return [
            {
                "content": item.content,
                "source": item.source,
                "added_at": item.added_at.isoformat(),
            }
            for item in self._items
        ]

    def clear(self) -> None:
        """Remove all items (call at goal start/end)."""
        self._items.clear()

    def most_recent(self, n: int = 3) -> list[WorkingMemoryItem]:
        """Return the N most recently added items, newest first."""
        items = list(self._items)
        return list(reversed(items[-n:]))

    def format_for_prompt(self, max_chars: int = 400) -> str:
        """Format recent items as a compact text block for prompt injection."""
        lines: list[str] = []
        total = 0
        for item in reversed(list(self._items)):
            line = f"[{item.source or 'ctx'}] {item.content[:120]}"
            total += len(line)
            if total > max_chars:
                break
            lines.append(line)
        return "\n".join(reversed(lines))

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[WorkingMemoryItem]:
        return iter(self._items)

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def is_full(self) -> bool:
        return len(self._items) >= self._capacity
