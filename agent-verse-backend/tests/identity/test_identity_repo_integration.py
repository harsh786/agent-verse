"""Phase 3 / 11 — durable identity + personalization stores against real Postgres."""

from __future__ import annotations

import os
import subprocess
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.chat.personalization_repo import PostgresPersonalizationStore
from app.identity.repository import PostgresIdentityStore
from app.identity.service import IdentityService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def sf() -> AsyncIterator[async_sessionmaker]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        admin_url = pg.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=_BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url},
            check=True,
            capture_output=True,
            text=True,
        )
        engine = create_async_engine(admin_url)
        yield async_sessionmaker(engine, expire_on_commit=False)
        await engine.dispose()


async def test_identity_cross_channel_durable(sf: async_sessionmaker) -> None:
    svc = IdentityService(PostgresIdentityStore(sf))
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    web = await svc.resolve_principal(tenant_id=tenant, channel="web", channel_user_id="u1")
    await svc.link_identity(
        tenant_id=tenant, principal_id=web.id, channel="whatsapp", channel_user_id="+1"
    )
    # A fresh service on the same DB resolves the linked WhatsApp id to the web principal.
    svc2 = IdentityService(PostgresIdentityStore(sf))
    same = await svc2.resolve_principal(
        tenant_id=tenant, channel="whatsapp", channel_user_id="+1"
    )
    assert same.id == web.id
    links = await svc2.identities_for(web.id, tenant)
    assert {(x.channel, x.channel_user_id) for x in links} == {
        ("web", "u1"), ("whatsapp", "+1")
    }


async def test_identity_is_tenant_scoped(sf: async_sessionmaker) -> None:
    svc = IdentityService(PostgresIdentityStore(sf))
    t1, t2 = f"t-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    p1 = await svc.resolve_principal(tenant_id=t1, channel="web", channel_user_id="shared")
    assert await svc.get_principal(p1.id, t2) is None


async def test_personalization_profile_durable(sf: async_sessionmaker) -> None:
    store = PostgresPersonalizationStore(sf)
    principal = f"t-{uuid.uuid4().hex[:8]}"
    await store.add_standing_instruction(principal, "always book aisle seats")
    await store.set_tone(principal, "concise")
    await store.set_preference(principal, "units", "metric")
    # Fresh store, same DB — profile persisted.
    prof = await PostgresPersonalizationStore(sf).get(principal)
    assert prof is not None
    assert prof.tone == "concise"
    assert prof.standing_instructions == ["always book aisle seats"]
    assert prof.preferences == {"units": "metric"}
    # De-dup on re-add (case-insensitive).
    await store.add_standing_instruction(principal, "Always book aisle seats")
    prof2 = await PostgresPersonalizationStore(sf).get(principal)
    assert prof2 is not None and len(prof2.standing_instructions) == 1
