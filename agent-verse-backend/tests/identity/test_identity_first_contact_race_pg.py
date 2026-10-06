"""a10-F249-02 (integration): concurrent first contacts converge on ONE principal.

``resolve_principal`` used to read the link (miss), create a principal, then
insert the link with ``ON CONFLICT DO NOTHING`` and return ITS principal either
way — so two racing first messages from one identity (two replicas, a
double-send) each got a different principal, the loser an orphan that then
claimed its own chat thread. Real Postgres, NOBYPASSRLS app role (FORCE RLS on
``principals`` / ``identity_links``), separate engines per "replica".

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/identity/test_identity_first_contact_race_pg.py -q -m integration
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.identity.models import IdentityLink, Principal
from app.identity.repository import PostgresIdentityStore
from app.identity.service import IdentityService
from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = pytest.mark.integration

REPLICAS = 8


@pytest.fixture(scope="module")
def env() -> Iterator[tuple[list[Any], str]]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        loop = asyncio.new_event_loop()
        engine = loop.run_until_complete(
            app_role_engine(url, ["principals", "identity_links"])
        )
        loop.close()
        factories = [
            sessionmaker_for(create_async_engine(engine.url, poolclass=NullPool))
            for _ in range(REPLICAS)
        ]
        yield factories, url


async def _count(url: str, sql: str, tenant: str) -> int:
    eng = create_async_engine(url, poolclass=NullPool)  # owner: sees every row
    try:
        async with eng.connect() as c:
            return int((await c.execute(text(sql), {"t": tenant})).scalar_one())
    finally:
        await eng.dispose()


async def _race(factories: list[Any], tenant: str, user: str) -> set[str]:
    services = [IdentityService(PostgresIdentityStore(f)) for f in factories]
    got = await asyncio.gather(
        *(
            s.resolve_principal(tenant_id=tenant, channel="whatsapp", channel_user_id=user)
            for s in services
        )
    )
    return {p.id for p in got}


def test_concurrent_first_contacts_get_one_principal_and_no_orphans(
    env: tuple[list[Any], str],
) -> None:
    factories, url = env
    loop = asyncio.new_event_loop()
    try:
        for attempt in range(5):
            tenant = f"t-race-{uuid.uuid4().hex[:8]}"
            ids = loop.run_until_complete(_race(factories, tenant, f"+1555000{attempt}"))
            assert len(ids) == 1, f"attempt {attempt}: {len(ids)} principals for one identity"
            principals = loop.run_until_complete(
                _count(url, "SELECT COUNT(*) FROM principals WHERE tenant_id = :t", tenant)
            )
            links = loop.run_until_complete(
                _count(url, "SELECT COUNT(*) FROM identity_links WHERE tenant_id = :t", tenant)
            )
            assert (principals, links) == (1, 1), (attempt, principals, links)
            # A later contact resolves to the same principal.
            again = loop.run_until_complete(_race(factories[:1], tenant, f"+1555000{attempt}"))
            assert again == ids
    finally:
        loop.close()


def test_a_dangling_link_is_repointed_not_left_broken(env: tuple[list[Any], str]) -> None:
    factories, url = env
    tenant = f"t-dangle-{uuid.uuid4().hex[:8]}"
    store = PostgresIdentityStore(factories[0])
    loop = asyncio.new_event_loop()
    try:
        # A link whose principal no longer exists.
        loop.run_until_complete(
            store.create_link(
                IdentityLink(
                    tenant_id=tenant, channel="sms", channel_user_id="+1", principal_id="gone"
                )
            )
        )
        fresh = Principal(tenant_id=tenant)
        got = loop.run_until_complete(
            store.claim_link(
                fresh,
                IdentityLink(
                    tenant_id=tenant, channel="sms", channel_user_id="+1", principal_id=fresh.id
                ),
            )
        )
        assert got.id == fresh.id
        link = loop.run_until_complete(store.get_link(tenant, "sms", "+1"))
        assert link is not None and link.principal_id == fresh.id
    finally:
        loop.close()


def test_link_identity_returns_the_binding_that_won(env: tuple[list[Any], str]) -> None:
    factories, _ = env
    tenant = f"t-link-{uuid.uuid4().hex[:8]}"
    svc = IdentityService(PostgresIdentityStore(factories[0]))
    loop = asyncio.new_event_loop()
    try:
        first = loop.run_until_complete(
            svc.link_identity(
                tenant_id=tenant, principal_id="p-a", channel="tg", channel_user_id="42"
            )
        )
        second = loop.run_until_complete(
            svc.link_identity(
                tenant_id=tenant, principal_id="p-b", channel="tg", channel_user_id="42"
            )
        )
        assert first.principal_id == "p-a"
        assert second.principal_id == "p-a"  # not silently re-pointed, and reported honestly
    finally:
        loop.close()
