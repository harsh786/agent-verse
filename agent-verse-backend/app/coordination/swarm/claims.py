"""Atomic in-memory reference implementation for fenced swarm claims."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from app.coordination.swarm.models import SwarmClaim


class StaleFencingTokenError(RuntimeError):
    pass


class ClaimRepository:
    def __init__(self) -> None:
        self._claims: dict[tuple[str, str], SwarmClaim] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        owner_agent_id: str,
        lease_duration: timedelta,
        now: datetime,
    ) -> SwarmClaim:
        if lease_duration <= timedelta(0):
            raise ValueError("lease duration must be positive")
        key = (tenant_id, work_item_id)
        async with self._lock:
            prior = self._claims.get(key)
            if prior is not None and prior.lease_expires_at > now and prior.state == "claimed":
                if prior.owner_agent_id == owner_agent_id:
                    return prior
                raise RuntimeError("work item is already claimed")
            claim = SwarmClaim(
                tenant_id=tenant_id,
                work_item_id=work_item_id,
                owner_agent_id=owner_agent_id,
                fencing_token=(prior.fencing_token + 1 if prior else 1),
                lease_expires_at=now + lease_duration,
                attempt=(prior.attempt + 1 if prior else 1),
            )
            self._claims[key] = claim
            return claim

    async def publish_result(
        self,
        tenant_id: str,
        work_item_id: str,
        *,
        owner_agent_id: str,
        fencing_token: int,
        result_reference: str,
    ) -> SwarmClaim:
        async with self._lock:
            key = (tenant_id, work_item_id)
            current = self._claims.get(key)
            if (
                current is None
                or current.owner_agent_id != owner_agent_id
                or current.fencing_token != fencing_token
            ):
                raise StaleFencingTokenError("stale swarm worker")
            completed = current.model_copy(
                update={"state": "completed", "result_reference": result_reference}
            )
            self._claims[key] = completed
            return completed


__all__ = ["ClaimRepository", "StaleFencingTokenError"]
