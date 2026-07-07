from __future__ import annotations
from collections import defaultdict


class BackpressureController:
    def __init__(self, max_depth: int = 10) -> None:
        self._max = max_depth
        self._queued: dict[str, int] = defaultdict(int)

    def record_queued(self, tenant_id: str) -> None:
        self._queued[tenant_id] += 1

    def record_completed(self, tenant_id: str) -> None:
        self._queued[tenant_id] = max(0, self._queued[tenant_id] - 1)

    def is_overloaded(self, tenant_id: str) -> bool:
        return self._queued[tenant_id] > self._max

    def queue_depth(self, tenant_id: str) -> int:
        return self._queued[tenant_id]
