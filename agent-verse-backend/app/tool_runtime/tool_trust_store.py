"""Per-process tool trust history (a cache; Postgres ``tool_trust_records`` is durable).

History is keyed by ``(tenant_id, tool_name)`` so one tenant's outcomes for a
same-named tool never move another tenant's score (TOOLCTX-04), and the number
of tracked keys is LRU-bounded so a long-lived process cannot grow without limit.
"""

from __future__ import annotations

from collections import OrderedDict, deque
from typing import Any

DEFAULT_MAX_KEYS = 10_000


class ToolTrustStore:
    def __init__(self, max_history: int = 100, max_keys: int = DEFAULT_MAX_KEYS) -> None:
        if max_keys < 1:
            raise ValueError("max_keys must be >= 1")
        self._history: OrderedDict[tuple[str, str], deque[dict[str, Any]]] = OrderedDict()
        self._max = max_history
        self._max_keys = max_keys

    def record_outcome(
        self, tool_name: str, *, success: bool, latency_ms: float, tenant_id: str = ""
    ) -> None:
        key = (tenant_id, tool_name)
        history = self._history.get(key)
        if history is None:
            history = deque(maxlen=self._max)
            self._history[key] = history
            while len(self._history) > self._max_keys:
                self._history.popitem(last=False)
        else:
            self._history.move_to_end(key)
        history.append({"success": success, "latency_ms": latency_ms})

    def get_history(self, tool_name: str, *, tenant_id: str = "") -> list[dict[str, Any]]:
        return list(self._history.get((tenant_id, tool_name), ()))

    def has_tool(self, tool_name: str, *, tenant_id: str = "") -> bool:
        return len(self._history.get((tenant_id, tool_name), ())) > 0

    def tracked_keys(self) -> int:
        return len(self._history)
