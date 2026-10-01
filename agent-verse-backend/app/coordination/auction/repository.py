"""Auction read models and opaque sealed-bid intake."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.coordination.state_repository import (
    InMemoryPatternStateRepository,
    PostgresPatternStateRepository,
)
from app.db.models.coordination import COORDINATION_TABLES
from app.db.rls import sqlalchemy_rls_context


class InMemoryAuctionRepository(InMemoryPatternStateRepository):
    pattern = "market_auction"


class PostgresAuctionRepository(PostgresPatternStateRepository):
    pattern = "market_auction"


class SealedBidReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str
    session_id: str
    bidder_id: str
    bid_version: int = Field(gt=0)
    envelope_digest: str
    submitted_at: datetime
    idempotency_key: str


class SealedEnvelope(BaseModel):
    """One stored (still sealed) bid envelope, as the auctioneer reads it at close."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bidder_digest: str
    bid_version: int
    ciphertext: str
    nonce: str
    signature: str
    submitted_at: datetime


def bidder_digest(bidder_id: str) -> str:
    return hashlib.sha256(bidder_id.encode()).hexdigest()


class InMemorySealedBidInbox:
    def __init__(self) -> None:
        self._receipts: dict[tuple[str, str], SealedBidReceipt] = {}
        self._versions: dict[tuple[str, str, str], int] = {}
        self._envelopes: dict[tuple[str, str], list[SealedEnvelope]] = {}
        self._lock = asyncio.Lock()

    async def submit(
        self,
        *,
        tenant_id: str,
        session_id: str,
        bidder_id: str,
        bid_version: int,
        ciphertext: str,
        nonce: str,
        signature: str,
        idempotency_key: str,
        auction_id: str | None = None,
    ) -> SealedBidReceipt:
        command = (tenant_id, idempotency_key)
        auction = auction_id or session_id
        async with self._lock:
            if command in self._receipts:
                return self._receipts[command]
            bidder = (tenant_id, auction, bidder_id)
            if bid_version <= self._versions.get(bidder, 0):
                raise ValueError("bid version must increase")
            digest = hashlib.sha256(f"{ciphertext}:{nonce}:{signature}".encode()).hexdigest()
            receipt = SealedBidReceipt(
                tenant_id=tenant_id,
                session_id=session_id,
                bidder_id=bidder_id,
                bid_version=bid_version,
                envelope_digest=digest,
                submitted_at=datetime.now(UTC),
                idempotency_key=idempotency_key,
            )
            self._versions[bidder] = bid_version
            self._receipts[command] = receipt
            self._envelopes.setdefault((tenant_id, auction), []).append(
                SealedEnvelope(
                    bidder_digest=bidder_digest(bidder_id),
                    bid_version=bid_version,
                    ciphertext=ciphertext,
                    nonce=nonce,
                    signature=signature,
                    submitted_at=receipt.submitted_at,
                )
            )
            return receipt

    async def envelopes(self, tenant_id: str, auction_id: str) -> tuple[SealedEnvelope, ...]:
        return tuple(self._envelopes.get((tenant_id, auction_id), ()))

    async def count(self, tenant_id: str, session_id: str) -> int:
        return sum(
            item.tenant_id == tenant_id and item.session_id == session_id
            for item in self._receipts.values()
        )


class PostgresSealedBidInbox:
    """Opaque durable intake using the canonical RLS-protected ``agent_bids`` table."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions
        self._table = COORDINATION_TABLES["agent_bids"]

    async def submit(
        self,
        *,
        tenant_id: str,
        session_id: str,
        bidder_id: str,
        bid_version: int,
        ciphertext: str,
        nonce: str,
        signature: str,
        idempotency_key: str,
        auction_id: str | None = None,
    ) -> SealedBidReceipt:
        command_id = uuid.uuid5(uuid.NAMESPACE_URL, f"sealed-bid:{tenant_id}:{idempotency_key}").hex
        digest_of_bidder = bidder_digest(bidder_id)
        # work_item_id carries the session; auction_id the registry auction (or the
        # session for legacy, registry-less intake).
        auction = auction_id or session_id
        envelope_digest = hashlib.sha256(f"{ciphertext}:{nonce}:{signature}".encode()).hexdigest()
        now = datetime.now(UTC)
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            prior = (
                (await db.execute(select(self._table).where(self._table.c.id == command_id)))
                .mappings()
                .one_or_none()
            )
            if prior is not None:
                return _receipt(prior, bidder_id=bidder_id, idempotency_key=idempotency_key)
            latest = (
                await db.execute(
                    select(func.max(self._table.c.bid_version)).where(
                        self._table.c.tenant_id == tenant_id,
                        self._table.c.auction_id == auction,
                        self._table.c.bidder_agent_id == digest_of_bidder,
                    )
                )
            ).scalar_one_or_none()
            if latest is not None and bid_version <= int(latest):
                raise ValueError("bid version must increase")
            await db.execute(
                insert(self._table).values(
                    id=command_id,
                    tenant_id=tenant_id,
                    work_item_id=session_id,
                    bidder_agent_id=digest_of_bidder,
                    sealed=True,
                    score=0,
                    auction_id=auction,
                    bid_version=bid_version,
                    governor_attestation="api-authenticated",
                    sealed_payload=ciphertext,
                    nonce=nonce,
                    signature=signature,
                    commitment=envelope_digest,
                    eligible=False,
                    invalid_reason=None,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        return SealedBidReceipt(
            tenant_id=tenant_id,
            session_id=session_id,
            bidder_id=bidder_id,
            bid_version=bid_version,
            envelope_digest=envelope_digest,
            submitted_at=now,
            idempotency_key=idempotency_key,
        )

    async def count(self, tenant_id: str, session_id: str) -> int:
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            value = await db.scalar(
                select(func.count())
                .select_from(self._table)
                .where(
                    self._table.c.tenant_id == tenant_id,
                    self._table.c.work_item_id == session_id,
                )
            )
        return int(value or 0)

    async def envelopes(self, tenant_id: str, auction_id: str) -> tuple[SealedEnvelope, ...]:
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            rows = (
                (
                    await db.execute(
                        select(self._table)
                        .where(
                            self._table.c.tenant_id == tenant_id,
                            self._table.c.auction_id == auction_id,
                        )
                        .order_by(self._table.c.created_at)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(
            SealedEnvelope(
                bidder_digest=str(row["bidder_agent_id"]),
                bid_version=int(str(row["bid_version"])),
                ciphertext=str(row["sealed_payload"]),
                nonce=str(row["nonce"]),
                signature=str(row["signature"]),
                submitted_at=row["created_at"],
            )
            for row in rows
        )


def _receipt(row: Any, *, bidder_id: str, idempotency_key: str) -> SealedBidReceipt:
    return SealedBidReceipt(
        tenant_id=str(row["tenant_id"]),
        session_id=str(row["work_item_id"]),
        bidder_id=bidder_id,
        bid_version=int(str(row["bid_version"])),
        envelope_digest=str(row["commitment"]),
        submitted_at=row["created_at"],
        idempotency_key=idempotency_key,
    )


__all__ = [
    "InMemoryAuctionRepository",
    "InMemorySealedBidInbox",
    "PostgresAuctionRepository",
    "PostgresSealedBidInbox",
    "SealedBidReceipt",
    "SealedEnvelope",
    "bidder_digest",
]
