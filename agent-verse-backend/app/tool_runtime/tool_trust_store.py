from __future__ import annotations
from collections import deque
from typing import Any


class ToolTrustStore:
    def __init__(self, max_history: int = 100) -> None:
        self._history: dict[str, deque[dict[str, Any]]] = {}
        self._max = max_history

    def record_outcome(self, tool_name: str, *, success: bool, latency_ms: float) -> None:
        if tool_name not in self._history:
            self._history[tool_name] = deque(maxlen=self._max)
        self._history[tool_name].append({"success": success, "latency_ms": latency_ms})

    def get_history(self, tool_name: str) -> list[dict[str, Any]]:
        return list(self._history.get(tool_name, []))

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._history and len(self._history[tool_name]) > 0
