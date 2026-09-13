"""Working memory — bounded short-term context window for the active agent run.

Analogous to human working memory: a small, fast, volatile store that holds
the most recently observed facts/tool outputs during a single goal execution.

Eviction is FIFO by default (oldest first). When a ``SalienceScorer`` is
supplied, the buffer instead keeps the most *salient* items under capacity
pressure and can recall them ranked by relevance to the run's focus — so a
bounded prompt block holds what matters, not merely what is newest. Working
memory is intentionally volatile: it lives in the checkpointed ``AgentState``
and dies with the run; it is NOT persisted to the durable canonical memory store.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.memory.salience import SalienceScorer


@dataclass
class WorkingMemoryItem:
    content: str
    source: str = ""  # e.g. "tool_output", "rag_chunk", "observation"
    metadata: dict[str, Any] = field(default_factory=dict)
    added_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    importance: float = 1.0  # caller-supplied weight multiplier
    access_count: int = 1  # bumped each time the item is recalled


class WorkingMemory:
    """A bounded queue of recent context items for the active goal.

    Parameters
    ----------
    capacity:
        Maximum number of items stored. When exceeded, an item is evicted.
    scorer:
        Optional :class:`SalienceScorer`. When provided, eviction and
        ``most_salient`` rank items by salience (relevance to ``focus`` +
        recency + access frequency) instead of pure FIFO.
    focus:
        The run's current query/goal text used as the default salience query.
    """

    def __init__(
        self,
        capacity: int = 10,
        *,
        scorer: SalienceScorer | None = None,
        focus: str = "",
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._scorer = scorer
        self._focus = focus
        self._items: list[WorkingMemoryItem] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_focus(self, query: str) -> None:
        """Set the default salience query (the run's current goal/step)."""
        self._focus = query

    def push(
        self,
        content: str,
        *,
        source: str = "",
        metadata: dict[str, Any] | None = None,
        importance: float = 1.0,
    ) -> WorkingMemoryItem:
        """Add an item, evicting one when capacity is exceeded.

        No scorer → evict the oldest (FIFO). With a scorer → evict the
        least-salient item so relevant context survives capacity pressure.
        """
        item = WorkingMemoryItem(
            content=content,
            source=source,
            metadata=metadata or {},
            importance=importance,
        )
        self._items.append(item)
        while len(self._items) > self._capacity:
            self._evict_one()
        return item

    def _evict_one(self) -> None:
        if not self._items:
            return
        if self._scorer is None:
            self._items.pop(0)  # FIFO: drop oldest
            return
        # Salience: drop the lowest-scoring item.
        victim = min(range(len(self._items)), key=lambda i: self._salience(self._items[i]))
        self._items.pop(victim)

    def _salience(self, item: WorkingMemoryItem, query: str | None = None) -> float:
        assert self._scorer is not None
        return (
            self._scorer.score(
                content=item.content,
                query=query if query is not None else self._focus,
                access_count=item.access_count,
                last_accessed_at=item.added_at,
            )
            * item.importance
        )

    def most_salient(self, n: int = 3, query: str | None = None) -> list[WorkingMemoryItem]:
        """Return the top-``n`` items ranked by salience (or recency without a scorer).

        Recalling bumps each returned item's ``access_count`` (frequency signal).
        """
        if self._scorer is None:
            ranked = list(reversed(self._items))  # newest first
        else:
            ranked = sorted(
                self._items,
                key=lambda it: self._salience(it, query),
                reverse=True,
            )
        top = ranked[:n]
        for item in top:
            item.access_count += 1
        return top

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
        return list(reversed(self._items[-n:]))

    def format_for_prompt(self, max_chars: int = 400, *, salient: bool = False) -> str:
        """Format items as a compact text block for prompt injection.

        ``salient=True`` (with a scorer) orders by salience; otherwise newest-first.
        """
        items = (
            self.most_salient(len(self._items))
            if salient and self._scorer is not None
            else list(reversed(self._items))
        )
        lines: list[str] = []
        total = 0
        for item in items:
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
