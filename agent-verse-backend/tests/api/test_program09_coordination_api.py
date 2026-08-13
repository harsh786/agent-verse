from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.coordination_auction import router as auction_router
from app.api.coordination_camel import router as camel_router
from app.api.coordination_generative import router as generative_router
from app.api.coordination_swarm import router as swarm_router
from app.coordination.auction.repository import InMemoryAuctionRepository, InMemorySealedBidInbox
from app.coordination.camel.repository import InMemoryCamelRepository
from app.coordination.generative.repository import InMemoryGenerativeRepository
from app.coordination.state_repository import PatternRecord
from app.coordination.swarm.repository import InMemorySwarmRepository
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware


def _app() -> FastAPI:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        if key in {"a", "b"}:
            return TenantContext(
                tenant_id=f"tenant-{key}", plan=PlanTier.PROFESSIONAL, api_key_id=key
            )
        return None

    repositories = {
        "camel_repository": InMemoryCamelRepository(),
        "generative_repository": InMemoryGenerativeRepository(),
        "swarm_repository": InMemorySwarmRepository(),
        "auction_repository": InMemoryAuctionRepository(),
    }
    for name, repository in repositories.items():
        asyncio.run(
            repository.save(
                PatternRecord(
                    tenant_id="tenant-a",
                    session_id="session",
                    execution_id=name,
                    state={"phase": "active", "secret": "[REDACTED]"},
                    version=1,
                    idempotency_key=name,
                ),
                expected_version=0,
            )
        )
        setattr(app.state, name, repository)
    app.state.auction_bid_inbox = InMemorySealedBidInbox()
    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    for router in (camel_router, generative_router, swarm_router, auction_router):
        app.include_router(router)
    return app


def test_program09_reads_are_authenticated_and_tenant_scoped() -> None:
    client = TestClient(_app())
    for suffix in ("camel", "generative", "swarm", "auction"):
        url = f"/api/v1/coordination/sessions/session/{suffix}"
        assert client.get(url).status_code == 401
        assert client.get(url, headers={"X-API-Key": "a"}).status_code == 200
        body = client.get(url, headers={"X-API-Key": "b"}).json()
        assert body.get("items", body.get("nodes")) == []


def test_auction_bid_command_returns_only_receipt_and_is_idempotent() -> None:
    client = TestClient(_app())
    body = {
        "bidder_id": "agent",
        "bid_version": 1,
        "ciphertext": "opaque",
        "nonce": "1234567890123456",
        "signature": "a" * 64,
    }
    headers = {"X-API-Key": "a", "Idempotency-Key": "bid-command"}
    first = client.post(
        "/api/v1/coordination/sessions/session/auction/bids", json=body, headers=headers
    )
    second = client.post(
        "/api/v1/coordination/sessions/session/auction/bids", json=body, headers=headers
    )
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert "ciphertext" not in first.json() and "signature" not in first.json()
