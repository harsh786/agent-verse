"""Per-auction key registry so public sealed bids can be opened and scored (AUCTION-KEYS).

``open_auction`` creates an announcement with an X25519 key pair and one HMAC
signing secret per eligible bidder. The private key and the secrets are stored only
as vault ciphertext bound to (tenant, auction); the public key and the bidders'
secrets are returned to the operator to distribute. ``accept_bid`` verifies a
public bid's signature and window before storing it (still sealed). ``close``
closes the window, decrypts every bidder's latest envelope, scores the valid ones
with the fixed-point policy and allocates the winner under a fencing token; the
result is persisted on the registry row and in the auction read model.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.coordination.auction.allocator import AuctionAllocator
from app.coordination.auction.bid_crypto import (
    BidEnvelopeError,
    generate_key_pair,
    new_signing_secret,
    open_bid,
    verify_signature,
)
from app.coordination.auction.models import (
    AuctionAnnouncement,
    RevealedBid,
    ScoreWeights,
)
from app.coordination.auction.repository import SealedEnvelope, bidder_digest
from app.coordination.auction.scoring import score_bids
from app.coordination.state_repository import PatternRecord
from app.db.models.coordination import AUCTION_REGISTRY
from app.db.rls import sqlalchemy_rls_context

DEFAULT_WEIGHTS = ScoreWeights(
    quality=4_000, cost=2_000, latency=1_000, confidence=2_000, fairness=500, load=500
)
SCORING_POLICY = "fixed-point-v1"


class AuctionRegistryError(Exception):
    status_code = 409


class AuctionNotFoundError(AuctionRegistryError):
    status_code = 404


class BidRejectedError(AuctionRegistryError):
    status_code = 403


class AuctionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    auction_id: str
    tenant_id: str
    session_id: str
    state: Literal["open", "closed"]
    deadline: datetime
    announcement: dict[str, Any]
    public_key: str
    sealed_keys: str
    result: dict[str, Any] | None = None
    closed_at: datetime | None = None
    idempotency_key: str

    def public(self) -> dict[str, Any]:
        return {
            "auction_id": self.auction_id,
            "session_id": self.session_id,
            "state": self.state,
            "deadline": self.deadline.isoformat(),
            "announcement": self.announcement,
            "public_key": self.public_key,
            "result": self.result,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
        }


class InMemoryAuctionRegistry:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str], AuctionRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, record: AuctionRecord) -> tuple[AuctionRecord, bool]:
        async with self._lock:
            for existing in self._records.values():
                if (
                    existing.tenant_id == record.tenant_id
                    and existing.session_id == record.session_id
                    and existing.idempotency_key == record.idempotency_key
                ):
                    return existing, True
            self._records[(record.tenant_id, record.auction_id)] = record
            return record, False

    async def get(self, tenant_id: str, auction_id: str) -> AuctionRecord | None:
        return self._records.get((tenant_id, auction_id))

    async def list_open(self, tenant_id: str, session_id: str) -> tuple[AuctionRecord, ...]:
        return tuple(
            item
            for item in self._records.values()
            if item.tenant_id == tenant_id
            and item.session_id == session_id
            and item.state == "open"
        )

    async def close(
        self, tenant_id: str, auction_id: str, *, result: dict[str, Any], closed_at: datetime
    ) -> AuctionRecord:
        async with self._lock:
            current = self._records[(tenant_id, auction_id)]
            if current.state == "closed":
                return current
            closed = current.model_copy(
                update={"state": "closed", "result": result, "closed_at": closed_at}
            )
            self._records[(tenant_id, auction_id)] = closed
            return closed


def _record_from_row(row: Any) -> AuctionRecord:
    return AuctionRecord(
        auction_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        session_id=str(row["session_id"]),
        state=row["state"],
        deadline=row["deadline"],
        announcement=dict(row["announcement"]),
        public_key=str(row["public_key"]),
        sealed_keys=str(row["sealed_keys"]),
        result=dict(row["result"]) if row["result"] is not None else None,
        closed_at=row["closed_at"],
        idempotency_key=str(row["idempotency_key"]),
    )


class PostgresAuctionRegistry:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(self, record: AuctionRecord) -> tuple[AuctionRecord, bool]:
        table = AUCTION_REGISTRY
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, record.tenant_id),
        ):
            prior = (
                (
                    await db.execute(
                        select(table).where(
                            table.c.tenant_id == record.tenant_id,
                            table.c.session_id == record.session_id,
                            table.c.idempotency_key == record.idempotency_key,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if prior is not None:
                return _record_from_row(prior), True
            await db.execute(
                insert(table).values(
                    id=record.auction_id,
                    tenant_id=record.tenant_id,
                    session_id=record.session_id,
                    state=record.state,
                    deadline=record.deadline,
                    announcement=record.announcement,
                    public_key=record.public_key,
                    sealed_keys=record.sealed_keys,
                    result=None,
                    closed_at=None,
                    idempotency_key=record.idempotency_key,
                )
            )
            return record, False

    async def get(self, tenant_id: str, auction_id: str) -> AuctionRecord | None:
        table = AUCTION_REGISTRY
        async with self._sessions() as db, db.begin(), sqlalchemy_rls_context(db, tenant_id):
            row = (
                (
                    await db.execute(
                        select(table).where(
                            table.c.tenant_id == tenant_id, table.c.id == auction_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return _record_from_row(row) if row is not None else None

    async def list_open(self, tenant_id: str, session_id: str) -> tuple[AuctionRecord, ...]:
        table = AUCTION_REGISTRY
        async with self._sessions() as db, db.begin(), sqlalchemy_rls_context(db, tenant_id):
            rows = (
                (
                    await db.execute(
                        select(table).where(
                            table.c.tenant_id == tenant_id,
                            table.c.session_id == session_id,
                            table.c.state == "open",
                        )
                    )
                )
                .mappings()
                .all()
            )
        return tuple(_record_from_row(row) for row in rows)

    async def close(
        self, tenant_id: str, auction_id: str, *, result: dict[str, Any], closed_at: datetime
    ) -> AuctionRecord:
        table = AUCTION_REGISTRY
        async with self._sessions() as db, db.begin(), sqlalchemy_rls_context(db, tenant_id):
            row = (
                (
                    await db.execute(
                        select(table)
                        .where(table.c.tenant_id == tenant_id, table.c.id == auction_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if row["state"] == "closed":
                return _record_from_row(row)
            await db.execute(
                update(table)
                .where(table.c.tenant_id == tenant_id, table.c.id == auction_id)
                .values(
                    state="closed",
                    result=result,
                    closed_at=closed_at,
                    version=table.c.version + 1,
                    updated_at=closed_at,
                )
            )
            closed = _record_from_row(row)
        return closed.model_copy(
            update={"state": "closed", "result": result, "closed_at": closed_at}
        )


class AuctionRegistryService:
    def __init__(
        self,
        *,
        registry: Callable[[], Any],
        bid_inbox: Callable[[], Any],
        read_model: Callable[[], Any],
        vault: Callable[[], Any],
    ) -> None:
        self._registry = registry
        self._inbox = bid_inbox
        self._read_model = read_model
        self._vault = vault

    # ── keys ────────────────────────────────────────────────────────────────
    def _seal_keys(self, tenant_id: str, auction_id: str, keys: dict[str, Any]) -> str:
        bound = {"tenant_id": tenant_id, "auction_id": auction_id, **keys}
        return str(self._vault().encrypt(json.dumps(bound, sort_keys=True)))

    def _open_keys(self, record: AuctionRecord) -> dict[str, Any]:
        keys = json.loads(self._vault().decrypt(record.sealed_keys))
        if keys.get("tenant_id") != record.tenant_id or keys.get("auction_id") != record.auction_id:
            # A row copied across tenants/auctions must never unseal anything.
            raise AuctionRegistryError("auction key material is bound to another auction")
        return dict(keys)

    def signing_secrets(self, record: AuctionRecord) -> dict[str, str]:
        return dict(self._open_keys(record)["signing_secrets"])

    # ── commands ────────────────────────────────────────────────────────────
    async def open_auction(
        self,
        *,
        tenant_id: str,
        session_id: str,
        objective: str,
        bidders: tuple[str, ...],
        maximum_cost: Decimal,
        window: timedelta,
        required_capabilities: frozenset[str],
        idempotency_key: str,
    ) -> tuple[AuctionRecord, dict[str, str]]:
        auction_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"auction:{tenant_id}:{session_id}:{idempotency_key}"
        ).hex
        private_key, public_key = generate_key_pair()
        secrets_by_bidder = {bidder: new_signing_secret() for bidder in bidders}
        deadline = datetime.now(UTC) + window
        record = AuctionRecord(
            auction_id=auction_id,
            tenant_id=tenant_id,
            session_id=session_id,
            state="open",
            deadline=deadline,
            announcement={
                "objective": objective,
                "work_item_id": f"{auction_id}:task",
                "eligible_bidder_ids": sorted(bidders),
                "required_capabilities": sorted(required_capabilities),
                "maximum_cost": str(maximum_cost),
                "weights": DEFAULT_WEIGHTS.model_dump(),
                "scoring_policy_version": SCORING_POLICY,
                "envelope": "x25519-hkdf-sha256-aes256gcm-v1",
                "signature": "hmac-sha256(auction:bidder:version:nonce:ciphertext)",
            },
            public_key=public_key,
            sealed_keys=self._seal_keys(
                tenant_id,
                auction_id,
                {"private_key": private_key, "signing_secrets": secrets_by_bidder},
            ),
            idempotency_key=idempotency_key,
        )
        stored, replayed = await self._registry().create(record)
        return stored, (self.signing_secrets(stored) if replayed else secrets_by_bidder)

    async def get(self, tenant_id: str, session_id: str, auction_id: str) -> AuctionRecord:
        record = await self._registry().get(tenant_id, auction_id)
        if record is None or record.session_id != session_id:
            raise AuctionNotFoundError("auction not found")
        return record

    async def resolve_open_auction(
        self, tenant_id: str, session_id: str, auction_id: str | None
    ) -> AuctionRecord | None:
        """The auction a public bid targets: explicit, else the session's only open one."""
        if auction_id:
            return await self.get(tenant_id, session_id, auction_id)
        open_auctions = await self._registry().list_open(tenant_id, session_id)
        if len(open_auctions) > 1:
            raise AuctionRegistryError("several auctions are open; name auction_id")
        return open_auctions[0] if open_auctions else None

    async def accept_bid(
        self,
        record: AuctionRecord,
        *,
        bidder_id: str,
        bid_version: int,
        ciphertext: str,
        nonce: str,
        signature: str,
        idempotency_key: str,
    ) -> Any:
        if record.state != "open" or datetime.now(UTC) >= record.deadline:
            raise AuctionRegistryError("auction is sealed")
        if bidder_id not in record.announcement["eligible_bidder_ids"]:
            raise BidRejectedError("bidder is not eligible for this auction")
        secret = self.signing_secrets(record).get(bidder_id)
        if secret is None or not verify_signature(
            secret,
            auction_id=record.auction_id,
            bidder_id=bidder_id,
            version=bid_version,
            nonce=nonce,
            ciphertext=ciphertext,
            signature=signature,
        ):
            raise BidRejectedError("invalid bid signature")
        return await self._inbox().submit(
            tenant_id=record.tenant_id,
            session_id=record.session_id,
            bidder_id=bidder_id,
            bid_version=bid_version,
            ciphertext=ciphertext,
            nonce=nonce,
            signature=signature,
            idempotency_key=idempotency_key,
            auction_id=record.auction_id,
        )

    async def close(self, tenant_id: str, session_id: str, auction_id: str) -> AuctionRecord:
        record = await self.get(tenant_id, session_id, auction_id)
        if record.state == "closed":
            return record
        closed_at = min(datetime.now(UTC), record.deadline)
        keys = self._open_keys(record)
        envelopes: tuple[SealedEnvelope, ...] = await self._inbox().envelopes(tenant_id, auction_id)
        eligible = list(record.announcement["eligible_bidder_ids"])
        by_digest = {bidder_digest(bidder): bidder for bidder in eligible}
        latest: dict[str, SealedEnvelope] = {}
        for envelope in envelopes:
            if envelope.submitted_at > closed_at:
                continue  # arrived after the window closed
            prior = latest.get(envelope.bidder_digest)
            if prior is None or envelope.bid_version > prior.bid_version:
                latest[envelope.bidder_digest] = envelope
        announcement = AuctionAnnouncement(
            auction_id=auction_id,
            tenant_id=tenant_id,
            work_item_id=str(record.announcement["work_item_id"]),
            deadline=closed_at,
            eligible_bidder_ids=frozenset(eligible),
            required_capabilities=frozenset(record.announcement["required_capabilities"]),
            maximum_cost=Decimal(str(record.announcement["maximum_cost"])),
            weights=ScoreWeights.model_validate(record.announcement["weights"]),
            scoring_policy_version=str(record.announcement["scoring_policy_version"]),
        )
        revealed: list[RevealedBid] = []
        rejected: list[dict[str, str]] = []
        for digest, envelope in sorted(latest.items()):
            bidder = by_digest.get(digest)
            if bidder is None:
                rejected.append({"bidder_digest": digest, "reason": "ineligible_bidder"})
                continue
            secret = keys["signing_secrets"].get(bidder, "")
            if not verify_signature(
                secret,
                auction_id=auction_id,
                bidder_id=bidder,
                version=envelope.bid_version,
                nonce=envelope.nonce,
                ciphertext=envelope.ciphertext,
                signature=envelope.signature,
            ):
                rejected.append({"bidder_id": bidder, "reason": "invalid_signature"})
                continue
            try:
                payload = open_bid(
                    keys["private_key"],
                    auction_id=auction_id,
                    bidder_id=bidder,
                    version=envelope.bid_version,
                    nonce=envelope.nonce,
                    ciphertext=envelope.ciphertext,
                )
            except BidEnvelopeError:
                rejected.append({"bidder_id": bidder, "reason": "undecryptable"})
                continue
            if not announcement.required_capabilities <= payload.capabilities:
                rejected.append({"bidder_id": bidder, "reason": "capability_mismatch"})
                continue
            if payload.cost > announcement.maximum_cost:
                rejected.append({"bidder_id": bidder, "reason": "over_cost_ceiling"})
                continue
            revealed.append(
                RevealedBid(
                    bidder_id=bidder,
                    version=envelope.bid_version,
                    submitted_at=envelope.submitted_at,
                    payload=payload,
                )
            )
        ranked = score_bids(announcement, tuple(revealed))
        bids = [
            {
                "bidder_id": item.bidder_id,
                "total_score": item.total_score,
                "cost": str(item.cost),
                "explanation": dict(item.explanation),
            }
            for item in ranked
        ]
        allocation: dict[str, Any] | None = None
        if ranked:
            winner = ranked[0]
            allocated = await AuctionAllocator().allocate(
                tenant_id=tenant_id,
                auction_id=auction_id,
                work_item_id=announcement.work_item_id,
                winner_agent_id=winner.bidder_id,
                fairness_adjustment=Decimal(0),
                explanation={"components": dict(winner.explanation), "total": winner.total_score},
                lease_expires_at=closed_at + timedelta(hours=1),
                idempotency_key=f"{auction_id}:allocate",
            )
            allocation = {
                "allocation_id": auction_id,
                "winner_id": winner.bidder_id,
                "score": winner.total_score,
                "fairness_adjustment": str(allocated.fairness_adjustment),
                "fencing_token": allocated.fencing_token,
                "state": allocated.state,
            }
        result = {
            "phase": "allocated" if allocation else "failed",
            "terminal_reason": None if allocation else "no_eligible_bids",
            "bids": bids,
            "rejected": rejected,
            "allocation": allocation,
            "sealed_bid_count": len(latest),
        }
        closed = await self._registry().close(
            tenant_id, auction_id, result=result, closed_at=closed_at
        )
        await self._project(closed, result)
        return closed

    async def _project(self, record: AuctionRecord, result: dict[str, Any]) -> None:
        """Mirror the outcome into the auction read model served by GET .../auction."""
        repository = self._read_model()
        if repository is None:
            return
        existing = await repository.get(record.tenant_id, record.session_id, record.auction_id)
        if existing is not None:
            return
        await repository.save(
            PatternRecord(
                tenant_id=record.tenant_id,
                session_id=record.session_id,
                execution_id=record.auction_id,
                state={
                    "config": {
                        "pattern": "market_auction",
                        "objective": record.announcement["objective"],
                        "participants": record.announcement["eligible_bidder_ids"],
                        "source": "public_sealed_bid_auction",
                    },
                    "checkpoint": None,
                    "view": {"auction_id": record.auction_id, **result},
                },
                version=1,
                idempotency_key=f"{record.session_id}:{record.auction_id}:v1",
            ),
            expected_version=0,
        )


__all__ = [
    "AuctionNotFoundError",
    "AuctionRecord",
    "AuctionRegistryError",
    "AuctionRegistryService",
    "BidRejectedError",
    "InMemoryAuctionRegistry",
    "PostgresAuctionRegistry",
]
