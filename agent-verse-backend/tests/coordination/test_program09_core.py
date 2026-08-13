from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.coordination.auction.models import AuctionAnnouncement, BidPayload, ScoreWeights
from app.coordination.auction.scoring import score_bids
from app.coordination.auction.sealed_bids import InMemorySealedBidService
from app.coordination.camel.inception import build_inception
from app.coordination.camel.models import RoleContract
from app.coordination.generative.simulation_clock import SimulationClock
from app.coordination.swarm.claims import ClaimRepository, StaleFencingTokenError
from app.coordination.swarm.gossip import GossipRouter
from app.coordination.swarm.models import GossipMessage


def _role(name: str, responsibility: str) -> RoleContract:
    return RoleContract(
        role_name=name,
        objective="Produce a verified answer",
        responsibilities=(responsibility,),
        prohibited_actions=("change policy",),
        tool_allowlist=frozenset({"search"}),
        connector_allowlist=frozenset({"docs"}),
        data_scopes=frozenset({"internal"}),
        authority_ceiling=frozenset({"read"}),
        communication_schema="camel.turn.v1",
        termination_conditions=("verified",),
        version=1,
    )


def test_camel_inception_is_deterministic_and_rejects_escalation() -> None:
    first = build_inception(
        (_role("researcher", "collect evidence"), _role("reviewer", "verify evidence")),
        available_tools=frozenset({"search"}),
        available_connectors=frozenset({"docs"}),
        platform_authority=frozenset({"read"}),
        maximum_context_characters=4_000,
    )
    second = build_inception(
        (_role("researcher", "collect evidence"), _role("reviewer", "verify evidence")),
        available_tools=frozenset({"search"}),
        available_connectors=frozenset({"docs"}),
        platform_authority=frozenset({"read"}),
        maximum_context_characters=4_000,
    )
    assert first.contract_digest == second.contract_digest
    escalated = _role("admin", "verify").model_copy(
        update={"authority_ceiling": frozenset({"write"})}
    )
    with pytest.raises(PermissionError):
        build_inception(
            (_role("researcher", "collect"), escalated),
            available_tools=frozenset({"search"}),
            available_connectors=frozenset({"docs"}),
            platform_authority=frozenset({"read"}),
            maximum_context_characters=4_000,
        )


def test_simulation_clock_is_monotonic_bounded_and_idempotent() -> None:
    start = datetime(2026, 8, 12, tzinfo=UTC)
    clock = SimulationClock(start=start, horizon=start + timedelta(hours=2), maximum_events=2)
    assert clock.advance(timedelta(minutes=30), event_id="one") == start + timedelta(minutes=30)
    assert clock.advance(timedelta(minutes=30), event_id="one") == start + timedelta(minutes=30)
    clock.freeze()
    with pytest.raises(RuntimeError, match="frozen"):
        clock.advance(timedelta(minutes=1), event_id="two")
    clock.resume()
    assert clock.advance(timedelta(minutes=30), event_id="two") == start + timedelta(hours=1)
    with pytest.raises(RuntimeError, match="event limit"):
        clock.advance(timedelta(minutes=1), event_id="three")


@pytest.mark.asyncio
async def test_swarm_claims_reject_stale_workers_and_reclaim_with_new_fence() -> None:
    repository = ClaimRepository()
    now = datetime.now(UTC)
    first = await repository.acquire(
        tenant_id="tenant", work_item_id="work", owner_agent_id="a",
        lease_duration=timedelta(seconds=5), now=now,
    )
    reclaimed = await repository.acquire(
        tenant_id="tenant", work_item_id="work", owner_agent_id="b",
        lease_duration=timedelta(seconds=5), now=now + timedelta(seconds=6),
    )
    assert reclaimed.fencing_token == first.fencing_token + 1
    with pytest.raises(StaleFencingTokenError):
        await repository.publish_result(
            "tenant", "work", owner_agent_id="a", fencing_token=first.fencing_token,
            result_reference="artifact://stale",
        )


def test_gossip_deduplicates_and_decrements_hops() -> None:
    router = GossipRouter(max_messages_per_origin=2)
    message = GossipMessage(
        event_id="event", tenant_id="tenant", civilization_id="civ",
        origin_agent_id="a", origin_credential="signed:a", message_type="advertisement",
        payload_digest="abc", expires_at=datetime.now(UTC) + timedelta(minutes=1), hops_remaining=2,
    )
    forwarded = router.accept(
        message,
        credential_validator=lambda agent, token: token == f"signed:{agent}",
    )
    assert forwarded is not None and forwarded.hops_remaining == 1
    assert router.accept(message, credential_validator=lambda *_: True) is None


@pytest.mark.asyncio
async def test_sealed_bids_are_secret_until_deadline_and_score_deterministically() -> None:
    deadline = datetime.now(UTC) + timedelta(minutes=1)
    announcement = AuctionAnnouncement(
        auction_id="auction", tenant_id="tenant", work_item_id="work", deadline=deadline,
        eligible_bidder_ids=frozenset({"a", "b"}), required_capabilities=frozenset({"research"}),
        maximum_cost=Decimal("10"), weights=ScoreWeights(
            quality=4000, cost=2500, latency=1500, confidence=1000, fairness=500, load=500
        ), scoring_policy_version="v1",
    )
    service = InMemorySealedBidService(signing_secrets={"a": b"a", "b": b"b"})
    for bidder, quality, cost in (("a", 9000, "6"), ("b", 8500, "4")):
        payload = BidPayload(
            quality=quality, cost=Decimal(cost), latency_ms=1000, confidence=9000,
            fairness=0, load=1000, capabilities=frozenset({"research"}), commitment="sha256:x",
        )
        envelope = service.seal(announcement, bidder, payload, version=1)
        await service.submit(announcement, envelope, now=deadline - timedelta(seconds=1))
    with pytest.raises(PermissionError):
        await service.unseal(
            announcement,
            actor_role="auctioneer",
            now=deadline - timedelta(seconds=1),
        )
    bids = await service.unseal(announcement, actor_role="auctioneer", now=deadline)
    assert [item.bidder_id for item in score_bids(announcement, bids)] == ["b", "a"]
    assert score_bids(announcement, bids) == score_bids(announcement, tuple(reversed(bids)))
