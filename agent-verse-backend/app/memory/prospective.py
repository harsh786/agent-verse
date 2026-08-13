"""Idempotent leased prospective-memory lifecycle."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ProspectiveMemory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    memory_id: str
    tenant_id: str
    intention: str
    due_at: datetime
    expires_at: datetime
    state: Literal[
        "pending", "scheduled", "due", "leased", "executing", "completed", "failed",
        "cancelled", "expired",
    ] = "pending"
    source_goal_id: str
    source_execution_id: str
    policy_snapshot: dict[str, Any]
    classification: str
    idempotency_key: str
    attempts: int = Field(default=0, ge=0)
    fencing_token: int = Field(default=0, ge=0)
    lease_expires_at: datetime | None = None
    result: dict[str, Any] | None = None


class ProspectiveMemoryService:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], ProspectiveMemory] = {}
        self._commands: dict[tuple[str, str], ProspectiveMemory] = {}
        self._lock = asyncio.Lock()

    async def create(self, item: ProspectiveMemory) -> ProspectiveMemory:
        command = (item.tenant_id, item.idempotency_key)
        async with self._lock:
            prior = self._commands.get(command)
            if prior is not None:
                return prior
            self._items[(item.tenant_id, item.memory_id)] = item
            self._commands[command] = item
            return item

    async def lease_due(
        self, tenant_id: str, *, now: datetime, lease_duration: timedelta
    ) -> tuple[ProspectiveMemory, ...]:
        leased: list[ProspectiveMemory] = []
        async with self._lock:
            for key, item in sorted(self._items.items()):
                if item.tenant_id != tenant_id or item.state not in {"pending", "leased"}:
                    continue
                if item.expires_at <= now:
                    self._items[key] = item.model_copy(update={"state": "expired"})
                    continue
                if item.due_at > now or (
                    item.state == "leased"
                    and item.lease_expires_at is not None
                    and item.lease_expires_at > now
                ):
                    continue
                accepted = item.model_copy(
                    update={
                        "state": "leased",
                        "attempts": item.attempts + 1,
                        "fencing_token": item.fencing_token + 1,
                        "lease_expires_at": now + lease_duration,
                    }
                )
                self._items[key] = accepted
                leased.append(accepted)
        return tuple(leased)

    async def complete(
        self,
        tenant_id: str,
        memory_id: str,
        *,
        fencing_token: int,
        authorized: bool,
        result: dict[str, Any],
    ) -> ProspectiveMemory:
        async with self._lock:
            key = (tenant_id, memory_id)
            item = self._items[key]
            if not authorized:
                raise PermissionError("prospective action no longer authorized")
            if item.state != "leased" or item.fencing_token != fencing_token:
                raise RuntimeError("stale prospective-memory lease")
            completed = item.model_copy(update={"state": "completed", "result": result})
            self._items[key] = completed
            return completed

    async def cancel(self, tenant_id: str, memory_id: str, *, reason: str) -> ProspectiveMemory:
        async with self._lock:
            key = (tenant_id, memory_id)
            item = self._items[key]
            if item.state in {"completed", "expired"}:
                raise RuntimeError("terminal prospective memory cannot be cancelled")
            cancelled = item.model_copy(
                update={"state": "cancelled", "result": {"cancellation_reason": reason}}
            )
            self._items[key] = cancelled
            return cancelled

    async def get(self, tenant_id: str, memory_id: str) -> ProspectiveMemory | None:
        return self._items.get((tenant_id, memory_id))


def prospective_id(tenant_id: str, idempotency_key: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"{tenant_id}:{idempotency_key}").hex


__all__ = ["ProspectiveMemory", "ProspectiveMemoryService", "prospective_id"]
