"""Behavioral tests for the fenced, atomic swarm claim repository."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.coordination.swarm.claims import ClaimRepository, StaleFencingTokenError

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_fresh_claim_gets_first_fencing_token_and_attempt() -> None:
    repo = ClaimRepository()
    claim = await repo.acquire(
        tenant_id="t",
        work_item_id="w",
        owner_agent_id="agent-a",
        lease_duration=timedelta(minutes=5),
        now=_NOW,
    )
    assert claim.fencing_token == 1 and claim.attempt == 1
    assert claim.state == "claimed" and claim.owner_agent_id == "agent-a"


@pytest.mark.asyncio
async def test_active_lease_blocks_other_owner_but_is_idempotent_for_holder() -> None:
    repo = ClaimRepository()
    held = await repo.acquire(
        tenant_id="t",
        work_item_id="w",
        owner_agent_id="agent-a",
        lease_duration=timedelta(minutes=5),
        now=_NOW,
    )
    with pytest.raises(RuntimeError, match="already claimed"):
        await repo.acquire(
            tenant_id="t",
            work_item_id="w",
            owner_agent_id="agent-b",
            lease_duration=timedelta(minutes=5),
            now=_NOW + timedelta(seconds=1),
        )
    # The current holder re-acquiring gets the exact same claim (no new fencing token).
    again = await repo.acquire(
        tenant_id="t",
        work_item_id="w",
        owner_agent_id="agent-a",
        lease_duration=timedelta(minutes=5),
        now=_NOW + timedelta(seconds=1),
    )
    assert again == held


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimable_with_monotonic_fencing_token() -> None:
    repo = ClaimRepository()
    first = await repo.acquire(
        tenant_id="t",
        work_item_id="w",
        owner_agent_id="agent-a",
        lease_duration=timedelta(minutes=5),
        now=_NOW,
    )
    # After the lease expires a different worker may take over.
    reclaimed = await repo.acquire(
        tenant_id="t",
        work_item_id="w",
        owner_agent_id="agent-b",
        lease_duration=timedelta(minutes=5),
        now=_NOW + timedelta(minutes=6),
    )
    assert reclaimed.owner_agent_id == "agent-b"
    assert reclaimed.fencing_token == first.fencing_token + 1
    assert reclaimed.attempt == 2


@pytest.mark.asyncio
async def test_publish_result_requires_current_owner_and_fencing_token() -> None:
    repo = ClaimRepository()
    claim = await repo.acquire(
        tenant_id="t",
        work_item_id="w",
        owner_agent_id="agent-a",
        lease_duration=timedelta(minutes=5),
        now=_NOW,
    )
    completed = await repo.publish_result(
        "t",
        "w",
        owner_agent_id="agent-a",
        fencing_token=claim.fencing_token,
        result_reference="result://w",
    )
    assert completed.state == "completed" and completed.result_reference == "result://w"


@pytest.mark.asyncio
async def test_publish_result_rejects_stale_fencing_token_and_wrong_owner() -> None:
    repo = ClaimRepository()
    first = await repo.acquire(
        tenant_id="t",
        work_item_id="w",
        owner_agent_id="agent-a",
        lease_duration=timedelta(minutes=5),
        now=_NOW,
    )
    # A newer worker fenced out the old one after lease expiry.
    await repo.acquire(
        tenant_id="t",
        work_item_id="w",
        owner_agent_id="agent-b",
        lease_duration=timedelta(minutes=5),
        now=_NOW + timedelta(minutes=6),
    )
    # The evicted worker's stale token is refused.
    with pytest.raises(StaleFencingTokenError, match="stale swarm worker"):
        await repo.publish_result(
            "t",
            "w",
            owner_agent_id="agent-a",
            fencing_token=first.fencing_token,
            result_reference="result://stale",
        )
    # Wrong owner with an otherwise-valid token is also refused.
    with pytest.raises(StaleFencingTokenError):
        await repo.publish_result(
            "t",
            "w",
            owner_agent_id="impostor",
            fencing_token=first.fencing_token + 1,
            result_reference="result://impostor",
        )


@pytest.mark.asyncio
async def test_acquire_rejects_non_positive_lease() -> None:
    repo = ClaimRepository()
    with pytest.raises(ValueError, match="lease duration must be positive"):
        await repo.acquire(
            tenant_id="t",
            work_item_id="w",
            owner_agent_id="agent-a",
            lease_duration=timedelta(0),
            now=_NOW,
        )
