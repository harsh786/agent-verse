"""AUCTION-KEYS unit checks: envelope crypto and key binding fail closed."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.coordination.auction.bid_crypto import (
    BidEnvelopeError,
    generate_key_pair,
    new_signing_secret,
    open_bid,
    seal_bid,
    verify_signature,
)
from app.coordination.auction.models import BidPayload
from app.coordination.auction.registry import (
    AuctionRegistryError,
    AuctionRegistryService,
    InMemoryAuctionRegistry,
)
from app.coordination.auction.repository import InMemoryAuctionRepository, InMemorySealedBidInbox
from app.providers.vault import CredentialVault

_PAYLOAD = BidPayload(
    quality=8_000,
    cost=Decimal("0.3"),
    latency_ms=10,
    confidence=9_000,
    fairness=0,
    load=0,
    capabilities=frozenset({"x"}),
    commitment="c",
)


def test_envelope_round_trip_and_tamper_detection() -> None:
    private, public = generate_key_pair()
    secret = new_signing_secret()
    ciphertext, nonce, signature = seal_bid(
        public, _PAYLOAD, auction_id="a1", bidder_id="b1", version=1, signing_secret=secret
    )
    context = {"auction_id": "a1", "bidder_id": "b1", "version": 1}
    assert open_bid(private, nonce=nonce, ciphertext=ciphertext, **context) == _PAYLOAD
    assert verify_signature(
        secret, nonce=nonce, ciphertext=ciphertext, signature=signature, **context
    )
    # Bound to (auction, bidder, version): replaying under another bidder fails.
    with pytest.raises(BidEnvelopeError):
        open_bid(
            private, auction_id="a1", bidder_id="b2", version=1, nonce=nonce, ciphertext=ciphertext
        )
    other_private, _ = generate_key_pair()
    with pytest.raises(BidEnvelopeError):
        open_bid(other_private, nonce=nonce, ciphertext=ciphertext, **context)
    assert not verify_signature(
        new_signing_secret(), nonce=nonce, ciphertext=ciphertext, signature=signature, **context
    )


@pytest.mark.asyncio
async def test_key_material_copied_to_another_auction_never_unseals() -> None:
    registry = InMemoryAuctionRegistry()
    service = AuctionRegistryService(
        registry=lambda: registry,
        bid_inbox=InMemorySealedBidInbox,
        read_model=InMemoryAuctionRepository,
        vault=lambda: CredentialVault(master_key="k"),
    )
    first, _ = await service.open_auction(
        tenant_id="tenant-a",
        session_id="s",
        objective="o",
        bidders=("b1", "b2"),
        maximum_cost=Decimal(1),
        window=timedelta(minutes=5),
        required_capabilities=frozenset(),
        idempotency_key="one",
    )
    second, _ = await service.open_auction(
        tenant_id="tenant-b",
        session_id="s",
        objective="o",
        bidders=("b1", "b2"),
        maximum_cost=Decimal(1),
        window=timedelta(minutes=5),
        required_capabilities=frozenset(),
        idempotency_key="two",
    )
    forged = second.model_copy(update={"sealed_keys": first.sealed_keys})
    with pytest.raises(AuctionRegistryError, match="bound to another auction"):
        service.signing_secrets(forged)
