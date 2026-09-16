"""Cross-instance DB persistence + tenant-isolation proofs for the stores converted
from in-memory to DB in the distributed-scale hardening pass.

Each test uses two independent store instances sharing one db_factory (= two pods),
proving a write on "pod A" is visible / consumable on a fresh "pod B" and that
tenant isolation holds. Requires DATABASE_URL + `alembic upgrade head`.

    source /tmp/av.env && uv run pytest tests/api/test_session_persistence_stores.py -q
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration

_DATABASE_URL = os.environ.get("DATABASE_URL", "")


@pytest.fixture
async def db_factory() -> AsyncIterator[async_sessionmaker]:
    if not _DATABASE_URL:
        pytest.skip("DATABASE_URL not set")
    engine = create_async_engine(_DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            await session.execute(text("SELECT 1 FROM agent_api_keys LIMIT 1"))
            await session.execute(text("SELECT 1 FROM chat_connected_services LIMIT 1"))
            await session.execute(text("SELECT 1 FROM department_memory_entries LIMIT 1"))
            await session.execute(text("SELECT 1 FROM magentic_review_tokens LIMIT 1"))
    except Exception as exc:  # pragma: no cover - env-dependent skip
        await engine.dispose()
        pytest.skip(f"persistence tables unavailable: {exc}")
    try:
        yield factory
    finally:
        await engine.dispose()


# ── API-key auth: DB-authoritative, cross-pod revocation (X1) ──────────────────


async def test_api_key_auth_cross_pod_revocation(db_factory: async_sessionmaker) -> None:
    """A key created on pod A resolves on a fresh pod B, and after pod A revokes it,
    pod B (which never saw the create or revoke) rejects it — proving auth is
    DB-authoritative, not per-pod in-memory."""
    from app.services.tenant_service import TenantService

    email = f"authpod-{uuid.uuid4().hex[:8]}@example.com"
    pod_a = TenantService(db_session_factory=db_factory)
    pod_b = TenantService(db_session_factory=db_factory)  # fresh in-memory
    created = await pod_a.create_tenant(name="AuthPod", email=email)
    raw_key = created["api_key"]
    tenant_id = created["tenant_id"]
    try:
        # Pod B never synced/created this key, yet resolves it from the DB.
        ctx = await pod_b.resolve_api_key(raw_key)
        assert ctx is not None and ctx.tenant_id == tenant_id
        # Pod A revokes; pod B must now reject it.
        keys = await pod_a.list_api_keys(tenant_id)
        await pod_a.revoke_api_key(tenant_id, keys[0]["key_id"])
        assert await pod_b.resolve_api_key(raw_key) is None
    finally:
        async with db_factory() as s, s.begin():
            await s.execute(
                text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id}
            )
            await s.execute(
                text("DELETE FROM api_keys WHERE tenant_id = :t"), {"t": tenant_id}
            )
            await s.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tenant_id})


async def _del(factory: async_sessionmaker, table: str, tenant_id: str) -> None:
    async with factory() as s, s.begin():
        await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
        await s.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tenant_id})


# ── Agent credentials ─────────────────────────────────────────────────────────


async def test_agent_credentials_cross_pod(db_factory: async_sessionmaker) -> None:
    from app.auth.agent_credentials import AgentCredentialStore

    tenant = f"t-{uuid.uuid4().hex[:8]}"
    try:
        a = AgentCredentialStore()
        a.set_db(db_factory)
        b = AgentCredentialStore()  # fresh pod
        b.set_db(db_factory)
        res = await a.create_key_async(agent_id="ag1", tenant_id=tenant, name="ci")
        kid = res["key_id"]
        listed = await b.list_for_agent_async("ag1", tenant)
        assert [k["name"] for k in listed] == ["ci"]
        # tenant isolation
        assert await b.list_for_agent_async("ag1", f"other-{tenant}") == []
        assert await b.revoke_async(kid, "ag1", tenant) is True
        active = [k for k in await a.list_for_agent_async("ag1", tenant) if k["is_active"]]
        assert active == []
    finally:
        await _del(db_factory, "agent_api_keys", tenant)


# ── Chat connected services ───────────────────────────────────────────────────


async def test_connected_services_cross_pod(db_factory: async_sessionmaker) -> None:
    from app.chat.services_api import ServicesAPI

    tenant = f"t-{uuid.uuid4().hex[:8]}"
    try:
        a = ServicesAPI()
        a.set_db(db_factory)
        b = ServicesAPI()
        b.set_db(db_factory)
        r = await a.initiate_connection_async(tenant, "GitHub", "https://github.com", ["repo"])
        sid = r["service_id"]
        lst = await b.list_services_async(tenant)
        assert [(s.name, s.status) for s in lst] == [("GitHub", "pending")]
        done = await b.complete_connection_async(sid, tenant)
        assert done is not None and done.status == "connected"
        assert (await a.list_services_async(tenant))[0].connected_at is not None
        assert await a.disconnect_service_async(sid, tenant) is True
        assert await a.list_services_async(tenant) == []
    finally:
        await _del(db_factory, "chat_connected_services", tenant)


# ── Department memory ─────────────────────────────────────────────────────────


async def test_department_memory_cross_pod(db_factory: async_sessionmaker) -> None:
    from app.memory.dept_memory import DepartmentMemory

    tenant = f"t-{uuid.uuid4().hex[:8]}"
    try:
        a = DepartmentMemory()
        a.set_db(db_factory)
        b = DepartmentMemory()
        b.set_db(db_factory)
        ent = await a.add(
            dept_id="eng", org_id="o1", tenant_id=tenant,
            content="Stack is FastAPI + Postgres", source="u1",
        )
        listed = await b.list_entries_async("eng", tenant)
        assert len(listed) == 1
        hits = await b.retrieve("eng", "what stack", tenant_id=tenant)
        assert len(hits) == 1
        corr = await b.correct("eng", ent.entry_id, "and pgvector", "u2", tenant_id=tenant)
        assert corr is not None and corr.confidence < 0.9  # correction lowers confidence
        # tenant isolation
        assert await b.list_entries_async("eng", f"other-{tenant}") == []
    finally:
        await _del(db_factory, "department_memory_entries", tenant)


# ── Magentic human-review tokens ──────────────────────────────────────────────


async def test_magentic_review_cross_pod_one_time_consume(
    db_factory: async_sessionmaker,
) -> None:
    from app.coordination.magentic.human_review import MagenticHumanReviewService

    tenant = f"t-{uuid.uuid4().hex[:8]}"
    try:
        a = MagenticHumanReviewService()
        a.set_db(db_factory)
        b = MagenticHumanReviewService()
        b.set_db(db_factory)
        await a.issue(tenant, "sess1", "tok-abc")
        decision = await b.submit(
            tenant, "sess1", token="tok-abc", approved=True, safe_note="ok"
        )
        assert decision.approved is True
        # A second submit anywhere must be rejected (atomic one-time consume).
        with pytest.raises(PermissionError):
            await a.submit(tenant, "sess1", token="tok-abc", approved=True, safe_note="x")
    finally:
        await _del(db_factory, "magentic_review_tokens", tenant)
