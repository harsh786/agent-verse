"""Sealed-bid market auction driver (minimal producer).

Nothing ever announced an auction, unsealed bids or allocated a winner. This driver
runs one sealed-bid round for the run objective:

announce -> each eligible bidder agent prices the task and submits a signed,
KMS-sealed envelope (also recorded opaquely in the durable bid inbox) -> the window
closes once every bidder has bid -> the auctioneer unseals, scores with the fixed
point policy and allocates under a fencing token -> the winner executes -> settle.

The envelope key and bidder signing secrets are per run and never persisted: only
this run can unseal its bids. Envelopes submitted through the public bid API carry
no key this runtime holds, so they are counted but cannot be unsealed here.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from app.coordination.auction.allocator import AuctionAllocator
from app.coordination.auction.models import AuctionAnnouncement, BidPayload, ScoreWeights
from app.coordination.auction.scoring import score_bids
from app.coordination.auction.sealed_bids import KMSSealedBidService, LocalAESGCMKMS
from app.coordination.auction.state_machine import AuctionState, transition
from app.coordination.pattern_runs.context import RunContext, RunOutcome

_WEIGHTS = ScoreWeights(
    quality=4_000, cost=2_000, latency=1_000, confidence=2_000, fairness=500, load=500
)
_BID_WINDOW = timedelta(minutes=5)


def _bounded_int(value: Any, *, high: int) -> int:
    try:
        return max(0, min(high, int(value)))
    except (TypeError, ValueError):
        return 0


def _cost(value: Any, ceiling: Decimal) -> Decimal:
    try:
        return max(Decimal(0), min(ceiling, Decimal(str(value))))
    except (InvalidOperation, ValueError):
        return ceiling


async def run_auction(ctx: RunContext, *, bid_inbox: Any) -> RunOutcome:
    auction_id = uuid.uuid5(uuid.NAMESPACE_URL, f"auction:{ctx.execution_id}").hex
    ceiling = Decimal(str(ctx.options.get("max_cost_usd", 1.0)))
    opened_at = datetime.now(UTC)
    announcement = AuctionAnnouncement(
        auction_id=auction_id,
        tenant_id=ctx.tenant_id,
        work_item_id=f"{ctx.execution_id}:task",
        deadline=opened_at + _BID_WINDOW,
        eligible_bidder_ids=frozenset(ctx.participants),
        required_capabilities=frozenset({"general"}),
        maximum_cost=ceiling,
        weights=_WEIGHTS,
        scoring_policy_version="fixed-point-v1",
    )
    sealer = KMSSealedBidService(
        kms=LocalAESGCMKMS(secrets.token_bytes(32)),
        signing_secrets={bidder: secrets.token_bytes(32) for bidder in ctx.participants},
    )
    phase = AuctionState(auction_id=auction_id, state="announced", round_number=0)
    phase = transition(phase, "bidding", idempotency_key=f"{auction_id}:bidding")

    async def persist(**parts: Any) -> None:
        await ctx.update_view(auction_id=auction_id, phase=phase.state, **parts)

    await persist(bidders=list(ctx.participants), sealed_bid_count=0)
    approaches: dict[str, str] = {}
    sealed_count = 0
    for bidder in ctx.participants:
        data = await ctx.llm.json(
            f"You are agent '{bidder}' bidding to perform this task: {ctx.objective}\n"
            f"Budget ceiling: {ceiling} USD. Estimate honestly. Return "
            '{"approach": str, "quality": 0-10000, "cost_usd": number, '
            '"latency_ms": int, "confidence": 0-10000}',
            step=f"bid-{bidder}",
        )
        approach = str(data.get("approach") or "")[:2_000]
        approaches[bidder] = approach
        payload = BidPayload(
            quality=_bounded_int(data.get("quality"), high=10_000),
            cost=_cost(data.get("cost_usd"), ceiling),
            latency_ms=_bounded_int(data.get("latency_ms"), high=10_000_000),
            confidence=_bounded_int(data.get("confidence"), high=10_000),
            fairness=0,
            load=0,
            capabilities=frozenset({"general"}),
            commitment=hashlib.sha256(approach.encode()).hexdigest(),
        )
        envelope = await sealer.seal(announcement, bidder, payload, version=1)
        try:
            await sealer.submit(announcement, envelope, now=datetime.now(UTC))
        except ValueError:
            continue  # the bid window closed before this bidder answered
        # Durable opaque record (no plaintext): the bidder id is namespaced per
        # auction so each run's bids start at version 1.
        await bid_inbox.submit(
            tenant_id=ctx.tenant_id,
            session_id=ctx.session_id,
            bidder_id=f"{bidder}@{auction_id[:12]}",
            bid_version=1,
            ciphertext=envelope.ciphertext,
            nonce=envelope.nonce,
            signature=envelope.signature,
            idempotency_key=f"{ctx.execution_id}:bid:{bidder}",
        )
        sealed_count += 1
        await ctx.say(
            bidder,
            f"[auction {auction_id[:8]}] sealed bid submitted",
            step=f"auction:bid:{bidder}",
        )
    # Every eligible bidder has answered: close the window now and unseal.
    closed_at = datetime.now(UTC)
    closed = announcement.model_copy(update={"deadline": min(closed_at, announcement.deadline)})
    phase = transition(phase, "sealed", idempotency_key=f"{auction_id}:sealed")
    revealed = await sealer.unseal(closed, actor_role="auctioneer", now=closed_at)
    ranked = score_bids(closed, revealed)
    phase = transition(phase, "scored", idempotency_key=f"{auction_id}:scored")
    bids = [
        {
            "bidder_id": item.bidder_id,
            "total_score": item.total_score,
            "cost": str(item.cost),
            "explanation": dict(item.explanation),
        }
        for item in ranked
    ]
    await persist(sealed_bid_count=sealed_count, bids=bids)
    if not ranked:
        phase = transition(phase, "failed", idempotency_key=f"{auction_id}:failed")
        await persist(sealed_bid_count=sealed_count, bids=bids)
        return RunOutcome(phase="failed", terminal_reason="no_eligible_bids", view={"bids": bids})
    winner = ranked[0]
    allocator = AuctionAllocator()
    allocation = await allocator.allocate(
        tenant_id=ctx.tenant_id,
        auction_id=auction_id,
        work_item_id=announcement.work_item_id,
        winner_agent_id=winner.bidder_id,
        fairness_adjustment=Decimal(0),
        explanation={"components": dict(winner.explanation), "total": winner.total_score},
        lease_expires_at=closed_at + timedelta(minutes=10),
        idempotency_key=f"{auction_id}:allocate",
    )
    phase = transition(phase, "allocated", idempotency_key=f"{auction_id}:allocated")
    allocation_view = {
        "allocation_id": auction_id,
        "winner_id": winner.bidder_id,
        "score": winner.total_score,
        "fairness_adjustment": str(allocation.fairness_adjustment),
        "fencing_token": allocation.fencing_token,
        "state": allocation.state,
    }
    await persist(bids=bids, allocation=allocation_view)
    phase = transition(phase, "executing", idempotency_key=f"{auction_id}:executing")
    outcome = await ctx.llm.text(
        f"You are '{winner.bidder_id}', winner of the auction. Deliver the task using your "
        f"proposed approach.\nTask: {ctx.objective}\nApproach: {approaches[winner.bidder_id]}",
        step="execute",
        max_tokens=1_200,
    )
    settled = await allocator.settle(
        ctx.tenant_id,
        auction_id,
        winner_agent_id=winner.bidder_id,
        fencing_token=allocation.fencing_token,
        outcome_reference=f"auction://{ctx.execution_id}/outcome",
        idempotency_key=f"{auction_id}:settle",
    )
    phase = transition(phase, "settled", idempotency_key=f"{auction_id}:settled")
    allocation_view["state"] = settled.state
    await ctx.say(winner.bidder_id, outcome, step="auction:outcome", message_type="decision")
    await persist(bids=bids, allocation=allocation_view)
    return RunOutcome(
        phase="completed",
        terminal_reason="settled",
        safe_output=outcome[:8_000],
        view={"bids": bids, "allocation": allocation_view, "sealed_bid_count": sealed_count},
    )


__all__ = ["run_auction"]
