"""Behavioral tests for the deduplicating, credentialed, TTL-limited gossip router."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.coordination.swarm.gossip import GossipRouter
from app.coordination.swarm.models import GossipMessage

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _message(
    *,
    event_id: str = "e1",
    tenant_id: str = "t1",
    origin: str = "agent-1",
    credential: str = "cred-1",
    hops: int = 4,
    ttl_seconds: int = 60,
) -> GossipMessage:
    return GossipMessage(
        event_id=event_id,
        tenant_id=tenant_id,
        civilization_id="civ",
        origin_agent_id=origin,
        origin_credential=credential,
        message_type="advertisement",
        payload_digest="digest",
        expires_at=_NOW + timedelta(seconds=ttl_seconds),
        hops_remaining=hops,
    )


def _valid(_agent: str, _credential: str) -> bool:
    return True


def test_gossip_router_rejects_non_positive_origin_limit() -> None:
    with pytest.raises(ValueError, match="origin limit"):
        GossipRouter(max_messages_per_origin=0)


def test_gossip_accept_decrements_hops_and_dedupes_by_event() -> None:
    router = GossipRouter(max_messages_per_origin=10)
    first = router.accept(_message(hops=4), credential_validator=_valid, now=_NOW)
    assert first is not None and first.hops_remaining == 3
    # A second sighting of the same (tenant, event) is suppressed.
    duplicate = router.accept(_message(hops=4), credential_validator=_valid, now=_NOW)
    assert duplicate is None


def test_gossip_drops_expired_and_hop_exhausted_messages() -> None:
    router = GossipRouter(max_messages_per_origin=10)
    expired = router.accept(
        _message(event_id="expired", ttl_seconds=1),
        credential_validator=_valid,
        now=_NOW + timedelta(seconds=5),
    )
    assert expired is None
    exhausted = router.accept(
        _message(event_id="dead", hops=0), credential_validator=_valid, now=_NOW
    )
    assert exhausted is None


def test_gossip_raises_on_forged_origin_credential() -> None:
    router = GossipRouter(max_messages_per_origin=10)
    with pytest.raises(PermissionError, match="forged swarm origin"):
        router.accept(
            _message(),
            credential_validator=lambda _agent, _cred: False,
            now=_NOW,
        )


def test_gossip_enforces_per_origin_budget_scoped_by_tenant() -> None:
    router = GossipRouter(max_messages_per_origin=2)
    for index in range(2):
        accepted = router.accept(
            _message(event_id=f"e{index}", origin="noisy"),
            credential_validator=_valid,
            now=_NOW,
        )
        assert accepted is not None
    # Third distinct event from the same origin exceeds its budget.
    blocked = router.accept(
        _message(event_id="e2", origin="noisy"), credential_validator=_valid, now=_NOW
    )
    assert blocked is None
    # A different origin still has its own budget.
    other_origin = router.accept(
        _message(event_id="e3", origin="quiet"), credential_validator=_valid, now=_NOW
    )
    assert other_origin is not None
    # The same origin under a different tenant has a separate budget bucket.
    other_tenant = router.accept(
        _message(event_id="e4", origin="noisy", tenant_id="t2"),
        credential_validator=_valid,
        now=_NOW,
    )
    assert other_tenant is not None


def test_gossip_ttl_boundary_expires_at_exact_now() -> None:
    router = GossipRouter(max_messages_per_origin=10)
    # expires_at == now must be treated as expired (<= comparison).
    result = router.accept(
        _message(event_id="edge", ttl_seconds=0), credential_validator=_valid, now=_NOW
    )
    assert result is None
