"""SessionMemory — within-session goal execution memory."""
from __future__ import annotations
from typing import Any


class SessionMemory:
    def __init__(self) -> None:
        self._data: dict[str, list[dict[str, Any]]] = {}

    def add(self, *, goal_id: str, key: str, value: Any) -> None:
        self._data.setdefault(goal_id, []).append({"key": key, "value": value})

    def get(self, *, goal_id: str) -> list[dict[str, Any]]:
        return list(self._data.get(goal_id, []))

    def clear(self, goal_id: str) -> None:
        self._data.pop(goal_id, None)
