"""Bounded prospective-memory execution used by Celery maintenance workers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from app.memory.prospective import ProspectiveMemory, ProspectiveMemoryService

ProspectiveHandler = Callable[[ProspectiveMemory], Awaitable[dict[str, Any]]]
PolicyCheck = Callable[[ProspectiveMemory], Awaitable[bool]]


async def process_due_memories(
    service: ProspectiveMemoryService,
    *,
    tenant_id: str,
    now: datetime,
    handler: ProspectiveHandler,
    authorize: PolicyCheck,
    lease_duration: timedelta = timedelta(minutes=5),
    maximum_items: int = 100,
) -> tuple[ProspectiveMemory, ...]:
    """Claim and complete a bounded batch; duplicate workers lose fencing races."""
    claimed = await service.lease_due(tenant_id, now=now, lease_duration=lease_duration)
    completed: list[ProspectiveMemory] = []
    for item in claimed[:maximum_items]:
        allowed = await authorize(item)
        if not allowed:
            continue
        result = await handler(item)
        completed.append(
            await service.complete(
                tenant_id,
                item.memory_id,
                fencing_token=item.fencing_token,
                authorized=True,
                result=result,
            )
        )
    return tuple(completed)


__all__ = ["PolicyCheck", "ProspectiveHandler", "process_due_memories"]
