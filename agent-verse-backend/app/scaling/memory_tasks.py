"""Bounded prospective-memory execution used by Celery maintenance workers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from app.memory.prospective import ProspectiveMemory, ProspectiveMemoryService

ProspectiveHandler = Callable[[ProspectiveMemory], Awaitable[dict[str, Any]]]
PolicyCheck = Callable[[ProspectiveMemory], Awaitable[bool]]


class SupportsPurge(Protocol):
    async def purge_expired(self, tenant_id: str, *, now: datetime) -> int: ...


async def purge_expired_memories(
    repository: SupportsPurge,
    *,
    tenant_id: str,
    now: datetime | None = None,
) -> int:
    """Actively enforce memory retention: hard-delete a tenant's expired rows.

    ``expires_at`` was previously only filtered at *read* time, so expired
    memories lingered in storage indefinitely. This drives the repository's
    tenant-scoped hard delete (the Postgres implementation runs inside its RLS
    context). Returns the number of rows removed.

    FOLLOW-UP: register this as a periodic Celery beat entry. It is intentionally
    not wired into app/scaling/celery_app.py here to avoid editing the shared
    beat schedule; expose it as a task and add the beat registration separately.
    """
    when = now if now is not None else datetime.now(UTC)
    return await repository.purge_expired(tenant_id, now=when)


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


__all__ = [
    "PolicyCheck",
    "ProspectiveHandler",
    "SupportsPurge",
    "process_due_memories",
    "purge_expired_memories",
]
