"""SVC-13 integration: SSO resolution is DB-authoritative across pods.

Two TenantService instances (= two pods) share a real migrated Postgres, read as
a NOBYPASSRLS app role, and a real Redis. The pod that did NOT provision the SSO
tenant must resolve the real persisted api_key_id, and after deactivation the
provisioning pod must refuse it.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/services/test_tenant_sso_integration.py -m integration --no-cov
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration


@pytest.fixture
async def app_db(pg_url: str) -> AsyncIterator[tuple[Any, Any]]:
    """(admin, app [NOBYPASSRLS]) session factories on the migrated container."""
    pw = secrets.token_urlsafe(16)
    role = f"it_sso_{secrets.token_hex(3)}"
    admin_engine = create_async_engine(pg_url, poolclass=NullPool)
    async with admin_engine.begin() as conn:
        q = (await conn.execute(text("SELECT quote_literal(:p)"), {"p": pw})).scalar_one()
        await conn.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD {q} NOBYPASSRLS"))
        await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
        await conn.execute(
            text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
        )
    url = make_url(pg_url).set(username=role, password=pw)
    app_engine = create_async_engine(url.render_as_string(hide_password=False), poolclass=NullPool)
    yield (
        async_sessionmaker(admin_engine, expire_on_commit=False),
        async_sessionmaker(app_engine, expire_on_commit=False),
    )
    await app_engine.dispose()
    await admin_engine.dispose()


@pytest.fixture
async def redis_client(redis_url: str) -> AsyncIterator[Any]:
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url, decode_responses=True)
    yield client
    await client.aclose()


async def test_sso_cross_pod_real_key_and_deactivation(
    app_db: tuple[Any, Any], redis_client: Any
) -> None:
    from app.services.tenant_service import TenantService

    _admin, app = app_db
    sub = f"kc|{uuid.uuid4().hex}"
    pod_a = TenantService(db_session_factory=app)
    pod_b = TenantService(db_session_factory=app)
    pod_a.set_redis(redis_client)
    pod_b.set_redis(redis_client)

    created = await pod_a.create_tenant_from_sso(
        sso_sub=sub, email=f"{uuid.uuid4().hex[:8]}@sso.test", name="SSO"
    )

    # Pod B never saw the provisioning, yet gets the real persisted key id.
    key = await pod_b.get_key_by_sso_sub(sso_sub=sub)
    assert key is not None
    assert key["key_id"] == created["api_key_id"]
    tenant_b = await pod_b.get_tenant_by_sso_sub(sso_sub=sub)
    assert tenant_b is not None and tenant_b["tenant_id"] == created["tenant_id"]
    assert await pod_b.resolve_api_key(created["api_key"]) is not None  # warm api_key cache

    # Deactivate on pod B: pod A (which provisioned it) must refuse SSO
    # and the API key, through the shared Redis entries as well.
    await pod_b.deactivate_tenant(created["tenant_id"])
    assert await pod_a.get_tenant_by_sso_sub(sso_sub=sub) is None
    assert await pod_a.get_key_by_sso_sub(sso_sub=sub) is None
    assert await pod_a.resolve_api_key(created["api_key"]) is None


async def test_sso_out_of_band_deactivation_honoured_without_cache(
    app_db: tuple[Any, Any],
) -> None:
    """No Redis: a deactivation written straight to the DB is seen on the next
    login on the pod that provisioned the tenant."""
    from app.services.tenant_service import TenantService

    admin, app = app_db
    sub = f"kc|{uuid.uuid4().hex}"
    pod = TenantService(db_session_factory=app)
    created = await pod.create_tenant_from_sso(
        sso_sub=sub, email=f"{uuid.uuid4().hex[:8]}@sso.test", name="SSO"
    )
    assert await pod.get_tenant_by_sso_sub(sso_sub=sub) is not None
    async with admin() as s, s.begin():
        await s.execute(
            text("UPDATE tenants SET is_active = false WHERE id = :t"),
            {"t": created["tenant_id"]},
        )
    assert await pod.get_tenant_by_sso_sub(sso_sub=sub) is None


async def test_api_keys_work_across_pods_with_nothing_mirrored(
    app_db: tuple[Any, Any], redis_client: Any
) -> None:
    """a08-F194-04 on the app role: signup on pod A, the key authenticates on
    pod B, a key minted and revoked on pod B is refused on pod A — and neither
    pod holds a copy of any tenant or key in memory."""
    from app.services.tenant_service import TenantService

    _admin, app = app_db
    pod_a = TenantService(db_session_factory=app)
    pod_b = TenantService(db_session_factory=app)
    pod_a.set_redis(redis_client)
    pod_b.set_redis(redis_client)

    created = await pod_a.create_tenant(name="Acme", email=f"{uuid.uuid4().hex[:8]}@k.test")
    tid = created["tenant_id"]
    ctx = await pod_b.resolve_api_key(created["api_key"])
    assert ctx is not None and ctx.tenant_id == tid and "admin" in ctx.roles

    ci = await pod_b.create_api_key(tenant_id=tid, name="CI", scopes=["goals:read"])
    assert (await pod_a.resolve_api_key(ci["raw_key"])) is not None  # cached now
    await pod_b.revoke_api_key(tid, ci["key_id"])
    assert await pod_a.resolve_api_key(ci["raw_key"]) is None

    for pod in (pod_a, pod_b):
        assert pod._tenants == {} and pod._keys == {} and pod._hash_to_key_id == {}
