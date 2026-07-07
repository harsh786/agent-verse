"""ReflexionStore — persistent failure lessons per tenant."""
from __future__ import annotations
from collections import deque
from typing import Any


class ReflexionStore:
    def __init__(self, max_per_tenant: int = 50) -> None:
        self._lessons: dict[str, deque[dict[str, Any]]] = {}
        self._max = max_per_tenant

    def record(
        self,
        *,
        tenant_id: str,
        lesson: str,
        source_goal_id: str,
        failure_class: str,
    ) -> None:
        if tenant_id not in self._lessons:
            self._lessons[tenant_id] = deque(maxlen=self._max)
        self._lessons[tenant_id].append({
            "lesson": lesson,
            "source_goal_id": source_goal_id,
            "failure_class": failure_class,
        })

    def recall(self, *, tenant_id: str, limit: int = 10) -> list[dict[str, Any]]:
        lessons = list(self._lessons.get(tenant_id, []))
        return lessons[-limit:]
