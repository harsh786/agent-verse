"""PostgresPersonalizationStore against real Postgres (testcontainers).

Proves per-principal profiles (tone, standing instructions, preferences) persist
across calls, that ``add_standing_instruction`` dedupes case-insensitively (same
semantics as ``InMemoryPersonalizationStore``, see tests/chat/test_personalization.py),
and that the store is tenant-scoped (principal_id is ``tenant`` or ``tenant:user``,
and rows are filtered by the derived tenant via RLS + an explicit filter).
"""

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

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def store() -> AsyncIterator[PostgresPersonalizationStore]:
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
        yield PostgresPersonalizationStore(async_sessionmaker(engine, expire_on_commit=False))
        await engine.dispose()


async def test_get_missing_profile_returns_none(store: PostgresPersonalizationStore) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    assert await store.get(tenant) is None


async def test_set_tone_creates_and_updates_profile(
    store: PostgresPersonalizationStore,
) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"

    prof = await store.set_tone(tenant, "  concise  ")
    assert prof.tone == "concise"
    got = await store.get(tenant)
    assert got is not None and got.tone == "concise"

    # Blank tone (after strip) clears it back to None rather than storing "".
    prof2 = await store.set_tone(tenant, "   ")
    assert prof2.tone is None
    assert (await store.get(tenant)).tone is None  # type: ignore[union-attr]


async def test_add_standing_instruction_dedupes_case_insensitively(
    store: PostgresPersonalizationStore,
) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"

    await store.add_standing_instruction(tenant, "always book aisle seats")
    prof = await store.add_standing_instruction(tenant, "Always Book Aisle Seats")
    assert prof.standing_instructions == ["Always Book Aisle Seats"]

    got = await store.get(tenant)
    assert got is not None
    assert got.standing_instructions == ["Always Book Aisle Seats"]


async def test_set_preference_upserts_key(store: PostgresPersonalizationStore) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"

    await store.set_preference(tenant, "units", "metric")
    prof = await store.set_preference(tenant, " units ", " imperial ")
    assert prof.preferences == {"units": "imperial"}

    got = await store.get(tenant)
    assert got is not None and got.preferences == {"units": "imperial"}


async def test_principal_id_with_user_suffix_derives_tenant(
    store: PostgresPersonalizationStore,
) -> None:
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    principal = f"{tenant}:alice"

    await store.set_tone(principal, "formal")
    got = await store.get(principal)
    assert got is not None and got.tone == "formal" and got.principal_id == principal

    # A sibling principal under the same tenant is a distinct row.
    assert await store.get(f"{tenant}:bob") is None


async def test_profiles_are_tenant_scoped(store: PostgresPersonalizationStore) -> None:
    t1, t2 = f"t-{uuid.uuid4().hex[:8]}", f"t-{uuid.uuid4().hex[:8]}"
    await store.set_tone(t1, "casual")

    # A different tenant never sees another tenant's profile.
    assert await store.get(t2) is None
