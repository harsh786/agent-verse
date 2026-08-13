"""Lease acquisition, heartbeats, fencing, expiry, and deterministic reclaim."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.coordination import COORDINATION_TABLES
from app.db.rls import sqlalchemy_rls_context


class ActiveClaimError(RuntimeError):
    """A non-expired claim already owns the work item."""


class StaleFencingTokenError(RuntimeError):
    """A stale owner attempted to mutate a reclaimed lease."""


class LeaseClaim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    work_item_id: str
    owner_agent_id: str
    lease_id: str
    fencing_token: int = Field(gt=0)
    heartbeat_at: datetime
    lease_expires_at: datetime
    state: str = "active"


class LeaseRepository(Protocol):
    async def acquire(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        owner_agent_id: str,
        now: datetime,
        ttl: timedelta,
    ) -> LeaseClaim: ...

    async def mutate(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        owner_agent_id: str,
        fencing_token: int,
        now: datetime,
        state: str | None = None,
        ttl: timedelta | None = None,
    ) -> LeaseClaim: ...


class InMemoryLeaseRepository:
    """Deterministic test repository with the same atomic lease semantics as PostgreSQL."""

    def __init__(self) -> None:
        self._claims: dict[tuple[str, str], LeaseClaim] = {}
        self._lock = asyncio.Lock()

    async def acquire(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        owner_agent_id: str,
        now: datetime,
        ttl: timedelta,
    ) -> LeaseClaim:
        async with self._lock:
            key = (tenant_id, work_item_id)
            current = self._claims.get(key)
            if (
                current is not None
                and current.state == "active"
                and current.lease_expires_at > now
            ):
                raise ActiveClaimError(f"work item already claimed: {work_item_id}")
            token = 1 if current is None else current.fencing_token + 1
            claim = LeaseClaim(
                tenant_id=tenant_id,
                work_item_id=work_item_id,
                owner_agent_id=owner_agent_id,
                lease_id=uuid.uuid4().hex,
                fencing_token=token,
                heartbeat_at=now,
                lease_expires_at=now + ttl,
            )
            self._claims[key] = claim
            return claim

    async def mutate(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        owner_agent_id: str,
        fencing_token: int,
        now: datetime,
        state: str | None = None,
        ttl: timedelta | None = None,
    ) -> LeaseClaim:
        async with self._lock:
            key = (tenant_id, work_item_id)
            current = self._claims.get(key)
            if (
                current is None
                or current.state != "active"
                or current.owner_agent_id != owner_agent_id
                or current.fencing_token != fencing_token
            ):
                raise StaleFencingTokenError(
                    f"stale fencing token for work item: {work_item_id}"
                )
            updated = current.model_copy(
                update={
                    "heartbeat_at": now,
                    "lease_expires_at": now + ttl if ttl is not None else current.lease_expires_at,
                    "state": state or current.state,
                }
            )
            self._claims[key] = updated
            return updated


class PostgresLeaseRepository:
    """Serialize claims per work item and persist monotonic fencing tokens."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def acquire(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        owner_agent_id: str,
        now: datetime,
        ttl: timedelta,
    ) -> LeaseClaim:
        work_items = COORDINATION_TABLES["work_items"]
        claims = COORDINATION_TABLES["claims"]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            work_item = (
                await db.execute(
                    select(work_items.c.id)
                    .where(work_items.c.id == work_item_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if work_item is None:
                raise KeyError(f"work item not found: {work_item_id}")

            current = (
                await db.execute(
                    select(claims)
                    .where(claims.c.work_item_id == work_item_id)
                    .with_for_update()
                )
            ).mappings().one_or_none()
            if (
                current is not None
                and current["state"] == "active"
                and current["lease_expires_at"] > now
            ):
                raise ActiveClaimError(f"work item already claimed: {work_item_id}")

            token = 1 if current is None else int(current["fencing_token"]) + 1
            values = {
                "owner_agent_id": owner_agent_id,
                "lease_id": uuid.uuid4().hex,
                "fencing_token": token,
                "heartbeat_at": now,
                "lease_expires_at": now + ttl,
                "state": "active",
            }
            if current is None:
                await db.execute(
                    insert(claims).values(
                        id=uuid.uuid4().hex,
                        tenant_id=tenant_id,
                        work_item_id=work_item_id,
                        **values,
                    )
                )
            else:
                await db.execute(
                    update(claims)
                    .where(claims.c.id == current["id"])
                    .values(**values, version=int(current["version"]) + 1, updated_at=now)
                )
            return LeaseClaim(tenant_id=tenant_id, work_item_id=work_item_id, **values)

    async def mutate(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        owner_agent_id: str,
        fencing_token: int,
        now: datetime,
        state: str | None = None,
        ttl: timedelta | None = None,
    ) -> LeaseClaim:
        claims = COORDINATION_TABLES["claims"]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            current = (
                await db.execute(
                    select(claims)
                    .where(claims.c.work_item_id == work_item_id)
                    .with_for_update()
                )
            ).mappings().one_or_none()
            if (
                current is None
                or current["state"] != "active"
                or current["owner_agent_id"] != owner_agent_id
                or int(current["fencing_token"]) != fencing_token
            ):
                raise StaleFencingTokenError(
                    f"stale fencing token for work item: {work_item_id}"
                )

            new_state = state or str(current["state"])
            expires_at = now + ttl if ttl is not None else current["lease_expires_at"]
            await db.execute(
                update(claims)
                .where(
                    claims.c.id == current["id"],
                    claims.c.version == current["version"],
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=expires_at,
                    state=new_state,
                    version=int(current["version"]) + 1,
                    updated_at=now,
                )
            )
            return LeaseClaim(
                tenant_id=tenant_id,
                work_item_id=work_item_id,
                owner_agent_id=owner_agent_id,
                lease_id=str(current["lease_id"]),
                fencing_token=fencing_token,
                heartbeat_at=now,
                lease_expires_at=expires_at,
                state=new_state,
            )


class LeaseManager:
    """Apply lease invariants through an atomic repository."""

    def __init__(self, repository: LeaseRepository) -> None:
        self._repository = repository

    async def acquire(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        owner_agent_id: str,
        now: datetime,
        ttl: timedelta,
    ) -> LeaseClaim:
        self._validate_ttl(ttl)
        return await self._repository.acquire(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            owner_agent_id=owner_agent_id,
            now=now,
            ttl=ttl,
        )

    async def heartbeat(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        owner_agent_id: str,
        fencing_token: int,
        now: datetime,
        ttl: timedelta,
    ) -> LeaseClaim:
        self._validate_ttl(ttl)
        return await self._repository.mutate(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            owner_agent_id=owner_agent_id,
            fencing_token=fencing_token,
            now=now,
            ttl=ttl,
        )

    async def complete(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        owner_agent_id: str,
        fencing_token: int,
        now: datetime,
    ) -> LeaseClaim:
        return await self._terminal(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            owner_agent_id=owner_agent_id,
            fencing_token=fencing_token,
            now=now,
            state="completed",
        )

    async def release(
        self,
        *,
        tenant_id: str,
        work_item_id: str,
        owner_agent_id: str,
        fencing_token: int,
        now: datetime,
    ) -> LeaseClaim:
        return await self._terminal(
            tenant_id=tenant_id,
            work_item_id=work_item_id,
            owner_agent_id=owner_agent_id,
            fencing_token=fencing_token,
            now=now,
            state="released",
        )

    async def _terminal(self, **values: object) -> LeaseClaim:
        return await self._repository.mutate(**values)  # type: ignore[arg-type]

    @staticmethod
    def _validate_ttl(ttl: timedelta) -> None:
        if ttl <= timedelta(0):
            raise ValueError("lease TTL must be positive")


__all__ = [
    "ActiveClaimError",
    "InMemoryLeaseRepository",
    "LeaseClaim",
    "LeaseManager",
    "LeaseRepository",
    "PostgresLeaseRepository",
    "StaleFencingTokenError",
]
