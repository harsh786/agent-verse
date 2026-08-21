"""Signed bid envelopes with deadline and replacement enforcement.

The in-memory implementation is a deterministic test/local adapter. Production KMS
implementations can keep this interface while replacing envelope cryptography.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from typing import Protocol

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.coordination.auction.models import (
    AuctionAnnouncement,
    BidPayload,
    RevealedBid,
    SealedBid,
)


class InMemorySealedBidService:
    def __init__(self, *, signing_secrets: dict[str, bytes]) -> None:
        self._secrets = signing_secrets
        self._bids: dict[tuple[str, str], SealedBid] = {}

    def seal(
        self,
        announcement: AuctionAnnouncement,
        bidder_id: str,
        payload: BidPayload,
        *,
        version: int,
        now: datetime | None = None,
    ) -> SealedBid:
        secret = self._secrets.get(bidder_id)
        if secret is None:
            raise PermissionError("bidder has no Governor-issued signing credential")
        submitted_at = now or datetime.now(UTC)
        serialized = payload.model_dump_json()
        nonce = secrets.token_hex(16)
        # This adapter avoids plaintext disclosure at the API/repository boundary; a
        # production deployment must substitute KMS-backed authenticated encryption.
        mask = hashlib.sha256(secret + nonce.encode()).digest()
        raw = serialized.encode()
        encrypted = bytes(value ^ mask[index % len(mask)] for index, value in enumerate(raw))
        ciphertext = base64.b64encode(encrypted).decode()
        signed = f"{announcement.auction_id}:{bidder_id}:{version}:{nonce}:{ciphertext}"
        signature = hmac.new(secret, signed.encode(), hashlib.sha256).hexdigest()
        return SealedBid(
            auction_id=announcement.auction_id,
            bidder_id=bidder_id,
            version=version,
            submitted_at=submitted_at,
            ciphertext=ciphertext,
            nonce=nonce,
            signature=signature,
        )

    async def submit(
        self,
        announcement: AuctionAnnouncement,
        envelope: SealedBid,
        *,
        now: datetime,
    ) -> SealedBid:
        if now >= announcement.deadline:
            raise ValueError("auction is sealed")
        if envelope.auction_id != announcement.auction_id:
            raise ValueError("bid targets a different auction")
        if envelope.bidder_id not in announcement.eligible_bidder_ids:
            raise PermissionError("bidder is not eligible")
        self._verify(envelope)
        key = (envelope.auction_id, envelope.bidder_id)
        prior = self._bids.get(key)
        if prior is not None and envelope.version <= prior.version:
            if envelope == prior:
                return prior
            raise ValueError("bid replacement version must increase")
        self._bids[key] = envelope
        return envelope

    async def unseal(
        self,
        announcement: AuctionAnnouncement,
        *,
        actor_role: str,
        now: datetime,
    ) -> tuple[RevealedBid, ...]:
        if actor_role != "auctioneer":
            raise PermissionError("only the authorized auctioneer may unseal")
        if now < announcement.deadline:
            raise PermissionError("bids cannot be unsealed before deadline")
        revealed: list[RevealedBid] = []
        for (auction_id, bidder_id), envelope in sorted(self._bids.items()):
            if auction_id != announcement.auction_id:
                continue
            self._verify(envelope)
            secret = self._secrets[bidder_id]
            mask = hashlib.sha256(secret + envelope.nonce.encode()).digest()
            encrypted = base64.b64decode(envelope.ciphertext)
            raw = bytes(value ^ mask[index % len(mask)] for index, value in enumerate(encrypted))
            payload = BidPayload.model_validate(json.loads(raw))
            if not announcement.required_capabilities <= payload.capabilities:
                continue
            if payload.cost > announcement.maximum_cost:
                continue
            revealed.append(
                RevealedBid(
                    bidder_id=bidder_id,
                    version=envelope.version,
                    submitted_at=envelope.submitted_at,
                    payload=payload,
                )
            )
        return tuple(revealed)

    def _verify(self, envelope: SealedBid) -> None:
        secret = self._secrets.get(envelope.bidder_id)
        if secret is None:
            raise PermissionError("unknown bidder credential")
        signed = (
            f"{envelope.auction_id}:{envelope.bidder_id}:{envelope.version}:"
            f"{envelope.nonce}:{envelope.ciphertext}"
        )
        expected = hmac.new(secret, signed.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, envelope.signature):
            raise PermissionError("invalid bid signature")


class BidEnvelopeKMS(Protocol):
    """KMS boundary; implementations retain key material outside the bid repository."""

    async def encrypt(self, plaintext: bytes, *, context: bytes) -> tuple[str, str]: ...

    async def decrypt(self, ciphertext: str, nonce: str, *, context: bytes) -> bytes: ...


class LocalAESGCMKMS:
    """Development/test KMS emulator using authenticated encryption, never XOR masking."""

    def __init__(self, key: bytes) -> None:
        if len(key) not in {16, 24, 32}:
            raise ValueError("AES-GCM key must contain 16, 24, or 32 bytes")
        self._cipher = AESGCM(key)

    async def encrypt(self, plaintext: bytes, *, context: bytes) -> tuple[str, str]:
        nonce = secrets.token_bytes(12)
        ciphertext = self._cipher.encrypt(nonce, plaintext, context)
        return base64.b64encode(ciphertext).decode(), base64.b64encode(nonce).decode()

    async def decrypt(self, ciphertext: str, nonce: str, *, context: bytes) -> bytes:
        return self._cipher.decrypt(base64.b64decode(nonce), base64.b64decode(ciphertext), context)


class KMSSealedBidService:
    """Authenticated KMS sealing with bidder signatures and deadline-gated unseal."""

    def __init__(self, *, kms: BidEnvelopeKMS, signing_secrets: dict[str, bytes]) -> None:
        self._kms = kms
        self._secrets = signing_secrets
        self._bids: dict[tuple[str, str], SealedBid] = {}

    async def seal(
        self,
        announcement: AuctionAnnouncement,
        bidder_id: str,
        payload: BidPayload,
        *,
        version: int,
        now: datetime | None = None,
    ) -> SealedBid:
        secret = self._secrets.get(bidder_id)
        if secret is None:
            raise PermissionError("bidder has no Governor-issued signing credential")
        context = _envelope_context(announcement.auction_id, bidder_id, version)
        ciphertext, nonce = await self._kms.encrypt(
            payload.model_dump_json().encode(), context=context
        )
        signature = hmac.new(
            secret, context + b":" + nonce.encode() + b":" + ciphertext.encode(), hashlib.sha256
        ).hexdigest()
        return SealedBid(
            auction_id=announcement.auction_id,
            bidder_id=bidder_id,
            version=version,
            submitted_at=now or datetime.now(UTC),
            ciphertext=ciphertext,
            nonce=nonce,
            signature=signature,
        )

    async def submit(
        self, announcement: AuctionAnnouncement, envelope: SealedBid, *, now: datetime
    ) -> SealedBid:
        if now >= announcement.deadline:
            raise ValueError("auction is sealed")
        if envelope.auction_id != announcement.auction_id:
            raise ValueError("bid targets a different auction")
        if envelope.bidder_id not in announcement.eligible_bidder_ids:
            raise PermissionError("bidder is not eligible")
        self._verify(envelope)
        key = (envelope.auction_id, envelope.bidder_id)
        prior = self._bids.get(key)
        if prior is not None and envelope.version <= prior.version:
            if prior == envelope:
                return prior
            raise ValueError("bid replacement version must increase")
        self._bids[key] = envelope
        return envelope

    async def unseal(
        self, announcement: AuctionAnnouncement, *, actor_role: str, now: datetime
    ) -> tuple[RevealedBid, ...]:
        if actor_role != "auctioneer":
            raise PermissionError("only the authorized auctioneer may unseal")
        if now < announcement.deadline:
            raise PermissionError("bids cannot be unsealed before deadline")
        revealed: list[RevealedBid] = []
        for (auction_id, bidder_id), envelope in sorted(self._bids.items()):
            if auction_id != announcement.auction_id:
                continue
            self._verify(envelope)
            raw = await self._kms.decrypt(
                envelope.ciphertext,
                envelope.nonce,
                context=_envelope_context(auction_id, bidder_id, envelope.version),
            )
            payload = BidPayload.model_validate(json.loads(raw))
            if announcement.required_capabilities <= payload.capabilities and (
                payload.cost <= announcement.maximum_cost
            ):
                revealed.append(
                    RevealedBid(
                        bidder_id=bidder_id,
                        version=envelope.version,
                        submitted_at=envelope.submitted_at,
                        payload=payload,
                    )
                )
        return tuple(revealed)

    def _verify(self, envelope: SealedBid) -> None:
        secret = self._secrets.get(envelope.bidder_id)
        if secret is None:
            raise PermissionError("unknown bidder credential")
        context = _envelope_context(envelope.auction_id, envelope.bidder_id, envelope.version)
        expected = hmac.new(
            secret,
            context + b":" + envelope.nonce.encode() + b":" + envelope.ciphertext.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, envelope.signature):
            raise PermissionError("invalid bid signature")


def _envelope_context(auction_id: str, bidder_id: str, version: int) -> bytes:
    return f"auction-bid-v1:{auction_id}:{bidder_id}:{version}".encode()


__all__ = [
    "BidEnvelopeKMS",
    "InMemorySealedBidService",
    "KMSSealedBidService",
    "LocalAESGCMKMS",
]
