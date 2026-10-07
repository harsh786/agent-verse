"""a10-F227-02 on real Postgres: the proactive_preferences opt-in table.

Migration c3e5a7b9d1f4 creates ``proactive_preferences`` (FORCE RLS). The store
reads/writes it under the tenant's RLS context as a least-privilege
(NOBYPASSRLS) role: a record round-trips, updates in place, is invisible to
another tenant, and the CHECK constraints refuse unsafe values.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.chat.proactive import ProactivePreferences
from app.proactive.preferences import ProactivePreferencesStore

pytestmark = pytest.mark.integration

_ROLE = "test_app_proactive_prefs"
_ROLE_PW = "proactive-prefs-test-pw"


async def _app_role(pg_url: str) -> tuple[Any, Any, Any]:
    owner_engine = create_async_engine(pg_url)
    owner = async_sessionmaker(owner_engine, expire_on_commit=False)
    async with owner() as s, s.begin():
        exists = (
            await s.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": _ROLE})
        ).first()
        if not exists:
            await s.execute(
                text(f"CREATE ROLE {_ROLE} LOGIN PASSWORD '{_ROLE_PW}' NOSUPERUSER NOBYPASSRLS")
            )
        await s.execute(text(f"GRANT USAGE ON SCHEMA public TO {_ROLE}"))
        await s.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON proactive_preferences TO {_ROLE}")
        )
    url = make_url(pg_url).set(username=_ROLE, password=_ROLE_PW)
    app_engine = create_async_engine(url)
    return owner_engine, app_engine, async_sessionmaker(app_engine, expire_on_commit=False)


async def test_preferences_round_trip_tenant_isolated(pg_url: str) -> None:
    owner_engine, app_engine, app_db = await _app_role(pg_url)
    store = ProactivePreferencesStore(SimpleNamespace(db_session_factory=app_db))
    t1, t2 = f"t1-{uuid.uuid4().hex[:8]}", f"t2-{uuid.uuid4().hex[:8]}"
    try:
        assert await store.get(t1, "p1") is None
        prefs = ProactivePreferences(
            enabled=True, quiet_hours=(22, 7), max_per_day=2,
            channels=frozenset({"web", "telegram"}), timezone="Asia/Tokyo",
        )
        await store.put(t1, "p1", prefs, updated_by="key-1")
        assert await store.get(t1, "p1") == prefs
        # Same principal id in another tenant: not opted in.
        assert await store.get(t2, "p1") is None

        changed = ProactivePreferences(enabled=False, max_per_day=1)
        await store.put(t1, "p1", changed)
        assert await store.get(t1, "p1") == changed

        assert not await store.delete(t2, "p1")  # cannot touch t1's row
        assert await store.get(t1, "p1") == changed
        assert await store.delete(t1, "p1")
        assert await store.get(t1, "p1") is None
    finally:
        await app_engine.dispose()
        await owner_engine.dispose()


@pytest.mark.parametrize(
    "prefs",
    [
        ProactivePreferences(max_per_day=21),
        ProactivePreferences(max_per_day=0),
        ProactivePreferences(quiet_hours=(24, 7)),
    ],
)
async def test_check_constraints_refuse_unsafe_values(
    pg_url: str, prefs: ProactivePreferences
) -> None:
    owner_engine, app_engine, app_db = await _app_role(pg_url)
    store = ProactivePreferencesStore(SimpleNamespace(db_session_factory=app_db))
    try:
        with pytest.raises(Exception, match="check"):
            await store.put(f"t-{uuid.uuid4().hex[:8]}", "p1", prefs)
    finally:
        await app_engine.dispose()
        await owner_engine.dispose()


async def test_rls_hides_rows_without_the_tenant_context(pg_url: str) -> None:
    owner_engine, app_engine, app_db = await _app_role(pg_url)
    store = ProactivePreferencesStore(SimpleNamespace(db_session_factory=app_db))
    tenant = f"t-{uuid.uuid4().hex[:8]}"
    try:
        await store.put(tenant, "p1", ProactivePreferences())
        async with app_db() as s:
            rows = (await s.execute(text("SELECT count(*) FROM proactive_preferences"))).scalar()
        assert rows == 0
    finally:
        await app_engine.dispose()
        await owner_engine.dispose()
