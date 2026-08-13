"""Tenant/persona scoped observation ingestion and bounded recall."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime

from app.coordination.generative.models import Observation


class ObservationStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Observation] = {}
        self._commands: dict[tuple[str, str], Observation] = {}
        self._lock = asyncio.Lock()

    async def add(self, observation: Observation) -> Observation:
        command = (observation.tenant_id, observation.idempotency_key)
        async with self._lock:
            if command in self._commands:
                return self._commands[command]
            poisoned = bool(
                re.search(
                    r"ignore previous|override policy|reveal secrets?",
                    observation.safe_summary,
                    re.IGNORECASE,
                )
            )
            accepted = observation.model_copy(update={"quarantined": poisoned})
            self._items[(accepted.tenant_id, accepted.observation_id)] = accepted
            self._commands[command] = accepted
            return accepted

    async def recall(
        self,
        tenant_id: str,
        persona_id: str,
        *,
        now: datetime,
        limit: int,
        maximum_classification: str = "restricted",
    ) -> tuple[Observation, ...]:
        order = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
        maximum = order[maximum_classification]
        eligible = (
            item
            for item in self._items.values()
            if item.tenant_id == tenant_id
            and item.persona_id == persona_id
            and not item.quarantined
            and item.expires_at > now
            and order[item.classification] <= maximum
        )
        return tuple(
            sorted(
                eligible,
                key=lambda item: (
                    -(item.importance + item.relevance + item.confidence),
                    -item.occurred_at.timestamp(),
                    item.observation_id,
                ),
            )[: max(0, min(limit, 100))]
        )


__all__ = ["ObservationStore"]
