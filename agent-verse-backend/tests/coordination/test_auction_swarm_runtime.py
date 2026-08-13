from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.coordination.auction.allocator import AuctionAllocator, StaleWinnerError
from app.coordination.auction.fairness import bounded_fairness_adjustment
from app.coordination.auction.state_machine import AuctionState, transition
from app.coordination.auction.threat_detection import BidIdentity, detect_threats
from app.coordination.swarm.convergence import ConvergenceItem, evaluate_convergence


def test_auction_state_machine_allows_only_declared_transitions() -> None:
    state = AuctionState(auction_id="a", state="announced", round_number=0)
    for target in ("bidding", "sealed", "scored", "allocated", "executing", "settled"):
        state = transition(state, target, idempotency_key=f"to:{target}")
    assert state.state == "settled"
    assert transition(state, "settled", idempotency_key="to:settled") == state
    with pytest.raises(ValueError):
        transition(state, "bidding", idempotency_key="illegal")


@pytest.mark.asyncio
async def test_allocator_is_idempotent_fenced_and_settles_once() -> None:
    allocator = AuctionAllocator()
    allocation = await allocator.allocate(
        tenant_id="tenant",
        auction_id="auction",
        work_item_id="work",
        winner_agent_id="a",
        fairness_adjustment=Decimal("0.1"),
        explanation={"score": 9000},
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        idempotency_key="allocate",
    )
    assert (
        await allocator.allocate(
            tenant_id="tenant",
            auction_id="auction",
            work_item_id="work",
            winner_agent_id="a",
            fairness_adjustment=Decimal("0.1"),
            explanation={"score": 9000},
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
            idempotency_key="allocate",
        )
        == allocation
    )
    with pytest.raises(StaleWinnerError):
        await allocator.settle(
            "tenant",
            "auction",
            winner_agent_id="b",
            fencing_token=allocation.fencing_token,
            outcome_reference="artifact://wrong",
            idempotency_key="wrong",
        )
    settled = await allocator.settle(
        "tenant",
        "auction",
        winner_agent_id="a",
        fencing_token=allocation.fencing_token,
        outcome_reference="artifact://result",
        idempotency_key="settle",
    )
    assert settled.state == "settled"


def test_fairness_is_bounded_and_threats_cover_sybil_and_duplicate_deployment() -> None:
    assert bounded_fairness_adjustment(opportunities=10, exposures=0, cap=500) == 500
    assert bounded_fairness_adjustment(opportunities=0, exposures=10, cap=500) == -500
    threats = detect_threats(
        (
            BidIdentity("a", "credential", "deployment", "sig-a"),
            BidIdentity("b", "credential", "deployment", "sig-b"),
        )
    )
    assert set(threats) == {"duplicate_credential", "duplicate_deployment"}


def test_swarm_convergence_requires_all_mandatory_work_and_stops_limits() -> None:
    assert evaluate_convergence(
        (
            ConvergenceItem(
                work_item_id="a",
                mandatory=True,
                state="completed",
                result_digest="x",
            ),
        ),
        criteria_met=True,
        spent=Decimal("1"),
        budget=Decimal("2"),
        deadline=datetime.now(UTC) + timedelta(minutes=1),
        repeated_results=0,
    ).converged
    stopped = evaluate_convergence(
        (ConvergenceItem(work_item_id="a", mandatory=True, state="pending"),),
        criteria_met=False,
        spent=Decimal("3"),
        budget=Decimal("2"),
        deadline=datetime.now(UTC) + timedelta(minutes=1),
        repeated_results=0,
    )
    assert stopped.terminal and stopped.reason == "budget_exceeded"
