"""AUCTION-KEYS integration: registry + sealed bids persist under RLS and open at close.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/coordination/test_auction_registry_postgres.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.coordination.auction.bid_crypto import seal_bid
from app.coordination.auction.models import BidPayload
from app.coordination.auction.registry import AuctionRegistryService, PostgresAuctionRegistry
from app.coordination.auction.repository import PostgresAuctionRepository, PostgresSealedBidInbox
from app.providers.vault import CredentialVault

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TENANTS = ("tenant-auction-a", "tenant-auction-b")


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


@pytest_asyncio.fixture
async def app_factory(postgres_url: str) -> AsyncIterator[Any]:
    password = secrets.token_urlsafe(24)
    role = f"test_app_auction_{secrets.token_hex(4)}"
    admin_engine = create_async_engine(postgres_url)
    async with admin_engine.begin() as conn:
        quoted = (
            await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
        ).scalar_one()
        await conn.execute(
            text(
                f"CREATE ROLE {role} LOGIN PASSWORD {quoted} "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        )
        await conn.execute(text(f"GRANT CONNECT ON DATABASE test TO {role}"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
        )
        for tenant in TENANTS:
            await conn.execute(
                text(
                    "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
                    "VALUES (:id, :id, :email, 'free', true) ON CONFLICT (id) DO NOTHING"
                ),
                {"id": tenant, "email": f"{tenant}@example.test"},
            )
    app_url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0)
    yield async_sessionmaker(app_engine, expire_on_commit=False)
    await app_engine.dispose()
    await admin_engine.dispose()


def _payload(quality: int) -> BidPayload:
    return BidPayload(
        quality=quality,
        cost=Decimal("0.25"),
        latency_ms=500,
        confidence=8_000,
        fairness=0,
        load=0,
        capabilities=frozenset(),
        commitment="c",
    )


@pytest.mark.asyncio
async def test_registry_bids_open_at_close_under_rls(app_factory: Any) -> None:
    registry = PostgresAuctionRegistry(app_factory)
    inbox = PostgresSealedBidInbox(app_factory)
    read_model = PostgresAuctionRepository(app_factory)
    service = AuctionRegistryService(
        registry=lambda: registry,
        bid_inbox=lambda: inbox,
        read_model=lambda: read_model,
        vault=lambda: CredentialVault(master_key="integration-master-key"),
    )
    tenant_a, tenant_b = TENANTS
    record, bidder_secrets = await service.open_auction(
        tenant_id=tenant_a,
        session_id="session-1",
        objective="Summarise the quarter",
        bidders=("alpha", "beta"),
        maximum_cost=Decimal("1"),
        window=timedelta(minutes=10),
        required_capabilities=frozenset(),
        idempotency_key="open-1",
    )
    for bidder, quality in (("alpha", 3_000), ("beta", 9_000)):
        ciphertext, nonce, signature = seal_bid(
            record.public_key,
            _payload(quality),
            auction_id=record.auction_id,
            bidder_id=bidder,
            version=1,
            signing_secret=bidder_secrets[bidder],
        )
        await service.accept_bid(
            record,
            bidder_id=bidder,
            bid_version=1,
            ciphertext=ciphertext,
            nonce=nonce,
            signature=signature,
            idempotency_key=f"bid-{bidder}",
        )
    assert await registry.get(tenant_b, record.auction_id) is None
    assert await inbox.envelopes(tenant_b, record.auction_id) == ()
    assert await inbox.count(tenant_a, "session-1") == 2

    closed = await service.close(tenant_a, "session-1", record.auction_id)
    assert closed.state == "closed"
    assert closed.result is not None
    assert closed.result["allocation"]["winner_id"] == "beta"
    reloaded = await registry.get(tenant_a, record.auction_id)
    assert reloaded is not None and reloaded.result == closed.result
    projected = await read_model.list_session(tenant_a, "session-1")
    assert projected[0].state["view"]["allocation"]["winner_id"] == "beta"
