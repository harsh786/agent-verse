"""Idempotent durable-intent repository for A2A tasks and callbacks."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class A2ATaskRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    civilization_id: str = Field(min_length=1)
    from_agent_id: str = Field(min_length=1)
    to_agent_id: str = Field(min_length=1)
    goal_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    context_reference: str = Field(min_length=1)
    callback_url: str | None = None
    status: Literal["pending", "accepted", "completed", "failed", "dead_letter"] = "pending"
    goal_id: str | None = None
    idempotency_key: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime


class InMemoryA2ARepository:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], A2ATaskRecord] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: A2ATaskRecord) -> A2ATaskRecord:
        async with self._lock:
            command = (record.tenant_id, record.idempotency_key)
            prior_id = self._idempotency.get(command)
            if prior_id is not None:
                return self._records[(record.tenant_id, prior_id)]
            self._records[(record.tenant_id, record.task_id)] = record
            self._idempotency[command] = record.task_id
            return record

    async def update(
        self, tenant_id: str, task_id: str, *, status: str, goal_id: str | None = None
    ) -> A2ATaskRecord:
        async with self._lock:
            key = (tenant_id, task_id)
            record = self._records[key]
            updated = record.model_copy(
                update={"status": status, "goal_id": goal_id, "updated_at": datetime.now(UTC)}
            )
            self._records[key] = updated
            return updated

    async def get(self, tenant_id: str, task_id: str) -> A2ATaskRecord | None:
        return self._records.get((tenant_id, task_id))


__all__ = ["A2ATaskRecord", "InMemoryA2ARepository"]
