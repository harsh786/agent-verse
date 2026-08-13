from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from cryptography.exceptions import InvalidTag

from app.coordination.auction.models import AuctionAnnouncement, BidPayload, ScoreWeights
from app.coordination.auction.sealed_bids import KMSSealedBidService, LocalAESGCMKMS


def _announcement(deadline: datetime) -> AuctionAnnouncement:
    return AuctionAnnouncement(
        auction_id="auction",
        tenant_id="tenant",
        work_item_id="work",
        deadline=deadline,
        eligible_bidder_ids=frozenset({"bidder"}),
        required_capabilities=frozenset({"research"}),
        maximum_cost=Decimal("10"),
        weights=ScoreWeights(
            quality=4000, cost=2500, latency=1500, confidence=1000, fairness=500, load=500
        ),
        scoring_policy_version="v1",
    )


@pytest.mark.asyncio
async def test_kms_bid_is_authenticated_and_deadline_gated() -> None:
    deadline = datetime.now(UTC) + timedelta(minutes=1)
    announcement = _announcement(deadline)
    service = KMSSealedBidService(
        kms=LocalAESGCMKMS(b"k" * 32), signing_secrets={"bidder": b"signing"}
    )
    payload = BidPayload(
        quality=9000,
        cost=Decimal("4"),
        latency_ms=100,
        confidence=9000,
        fairness=0,
        load=100,
        capabilities=frozenset({"research"}),
        commitment="sha256:evidence",
    )
    envelope = await service.seal(announcement, "bidder", payload, version=1)
    assert payload.model_dump_json() not in envelope.ciphertext
    await service.submit(announcement, envelope, now=deadline - timedelta(seconds=1))
    with pytest.raises(PermissionError, match="deadline"):
        await service.unseal(
            announcement, actor_role="auctioneer", now=deadline - timedelta(seconds=1)
        )
    revealed = await service.unseal(
        announcement, actor_role="auctioneer", now=deadline
    )
    assert revealed[0].payload == payload


@pytest.mark.asyncio
async def test_kms_ciphertext_cannot_move_between_envelope_contexts() -> None:
    kms = LocalAESGCMKMS(b"k" * 32)
    ciphertext, nonce = await kms.encrypt(b"secret", context=b"auction:a")
    with pytest.raises(InvalidTag):
        await kms.decrypt(ciphertext, nonce, context=b"auction:b")
