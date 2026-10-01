"""AUCTION-KEYS: public sealed bids are opened at close and scored, end to end via the API."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.coordination_auction import router as auction_router
from app.coordination.auction.bid_crypto import seal_bid
from app.coordination.auction.models import BidPayload
from app.coordination.auction.registry import AuctionRegistryService, InMemoryAuctionRegistry
from app.coordination.auction.repository import InMemoryAuctionRepository, InMemorySealedBidInbox
from app.coordination.contracts import AuthorizationContext
from app.coordination.service import CoordinationService, SessionAdmission
from app.coordination.store import InMemoryCoordinationStore
from app.providers.vault import CredentialVault
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_KEYS = {
    "op": TenantContext(
        tenant_id="tenant", plan=PlanTier.PROFESSIONAL, api_key_id="op", roles=("operator",)
    ),
    "viewer": TenantContext(
        tenant_id="tenant", plan=PlanTier.PROFESSIONAL, api_key_id="v", roles=("viewer",)
    ),
    "other": TenantContext(
        tenant_id="other", plan=PlanTier.PROFESSIONAL, api_key_id="x", roles=("operator",)
    ),
}


def _client() -> tuple[TestClient, str, Any]:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return _KEYS.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(auction_router)
    app.state.coordination_service = CoordinationService(InMemoryCoordinationStore())
    app.state.auction_repository = InMemoryAuctionRepository()
    app.state.auction_bid_inbox = InMemorySealedBidInbox()
    app.state.auction_registry = InMemoryAuctionRegistry()
    vault = CredentialVault(master_key="auction-test-master-key")
    app.state.auction_registry_service = AuctionRegistryService(
        registry=lambda: app.state.auction_registry,
        bid_inbox=lambda: app.state.auction_bid_inbox,
        read_model=lambda: app.state.auction_repository,
        vault=lambda: vault,
    )
    created = asyncio.run(
        app.state.coordination_service.create_session(
            _KEYS["op"],
            SessionAdmission(
                civilization_id="civ",
                goal_id="goal",
                policy_snapshot={},
                budget_snapshot={},
                authorization=AuthorizationContext(
                    actor_id="op", permissions=frozenset({"coordination:create"})
                ),
            ),
        )
    )
    return TestClient(app), str(created.session_id), app.state


def _payload(quality: int, cost: str) -> BidPayload:
    return BidPayload(
        quality=quality,
        cost=Decimal(cost),
        latency_ms=1_000,
        confidence=7_000,
        fairness=0,
        load=0,
        capabilities=frozenset({"writing"}),
        commitment="c" * 64,
    )


def _bid(
    client: TestClient,
    session_id: str,
    auction: dict[str, Any],
    bidder: str,
    payload: BidPayload,
    *,
    secret: str | None = None,
    key: str = "op",
    version: int = 1,
) -> Any:
    ciphertext, nonce, signature = seal_bid(
        auction["public_key"],
        payload,
        auction_id=auction["auction_id"],
        bidder_id=bidder,
        version=version,
        signing_secret=secret or auction["bidder_signing_secrets"][bidder],
    )
    return client.post(
        f"/api/v1/coordination/sessions/{session_id}/auction/bids",
        headers={"X-API-Key": key, "Idempotency-Key": f"{bidder}-{version}"},
        json={
            "bidder_id": bidder,
            "bid_version": version,
            "ciphertext": ciphertext,
            "nonce": nonce,
            "signature": signature,
            "auction_id": auction["auction_id"],
        },
    )


def _open(client: TestClient, session_id: str, key: str = "op") -> Any:
    return client.post(
        f"/api/v1/coordination/sessions/{session_id}/auctions",
        headers={"X-API-Key": key, "Idempotency-Key": "auction-1"},
        json={
            "objective": "Write the launch brief",
            "eligible_bidder_ids": ["agent-a", "agent-b", "agent-c"],
            "maximum_cost": "1.00",
            "bid_window_seconds": 600,
            "required_capabilities": ["writing"],
        },
    )


def test_public_bids_are_unsealed_scored_and_allocated_at_close() -> None:
    client, session_id, state = _client()
    opened = _open(client, session_id)
    assert opened.status_code == 201, opened.text
    auction = opened.json()
    assert set(auction["bidder_signing_secrets"]) == {"agent-a", "agent-b", "agent-c"}
    # Nothing secret is persisted in the clear.
    record = asyncio.run(state.auction_registry.get("tenant", auction["auction_id"]))
    for secret in auction["bidder_signing_secrets"].values():
        assert secret not in record.sealed_keys

    assert _bid(client, session_id, auction, "agent-a", _payload(5_000, "0.50")).status_code == 200
    # Revised bid: only the latest version counts at close.
    assert _bid(client, session_id, auction, "agent-b", _payload(4_000, "0.90")).status_code == 200
    revised = _bid(client, session_id, auction, "agent-b", _payload(9_500, "0.40"), version=2)
    assert revised.status_code == 200
    forged = _bid(
        client,
        session_id,
        auction,
        "agent-c",
        _payload(10_000, "0.01"),
        secret=auction["bidder_signing_secrets"]["agent-a"],
    )
    assert forged.status_code == 403
    outsider = client.post(
        f"/api/v1/coordination/sessions/{session_id}/auction/bids",
        headers={"X-API-Key": "op", "Idempotency-Key": "outsider"},
        json={
            "bidder_id": "intruder",
            "bid_version": 1,
            "ciphertext": "x" * 64,
            "nonce": "n" * 16,
            "signature": "s" * 64,
            "auction_id": auction["auction_id"],
        },
    )
    assert outsider.status_code == 403

    status_before = client.get(
        f"/api/v1/coordination/sessions/{session_id}/auctions/{auction['auction_id']}",
        headers={"X-API-Key": "op"},
    ).json()
    assert status_before["state"] == "open" and status_before["result"] is None
    assert "bidder_signing_secrets" not in status_before

    assert (
        client.post(
            f"/api/v1/coordination/sessions/{session_id}/auctions/{auction['auction_id']}/close",
            headers={"X-API-Key": "viewer"},
        ).status_code
        == 403
    )
    closed = client.post(
        f"/api/v1/coordination/sessions/{session_id}/auctions/{auction['auction_id']}/close",
        headers={"X-API-Key": "op"},
    )
    assert closed.status_code == 200, closed.text
    result = closed.json()["result"]
    assert result["allocation"]["winner_id"] == "agent-b"
    assert [bid["bidder_id"] for bid in result["bids"]] == ["agent-b", "agent-a"]
    assert result["sealed_bid_count"] == 2

    read_model = client.get(
        f"/api/v1/coordination/sessions/{session_id}/auction", headers={"X-API-Key": "op"}
    ).json()
    assert read_model["items"][0]["winner_id"] == "agent-b"
    late = _bid(client, session_id, auction, "agent-a", _payload(9_999, "0.10"), version=2)
    assert late.status_code == 409
    # Closing again is idempotent.
    again = client.post(
        f"/api/v1/coordination/sessions/{session_id}/auctions/{auction['auction_id']}/close",
        headers={"X-API-Key": "op"},
    )
    assert again.json()["result"] == result


def test_auction_registry_is_tenant_scoped_and_operator_only() -> None:
    client, session_id, _ = _client()
    assert _open(client, session_id, key="viewer").status_code == 403
    assert _open(client, session_id, key="other").status_code == 404
    auction = _open(client, session_id).json()
    assert (
        client.get(
            f"/api/v1/coordination/sessions/{session_id}/auctions/{auction['auction_id']}",
            headers={"X-API-Key": "other"},
        ).status_code
        == 404
    )
    replay = _open(client, session_id).json()
    assert replay["auction_id"] == auction["auction_id"]
    assert replay["bidder_signing_secrets"] == auction["bidder_signing_secrets"]
