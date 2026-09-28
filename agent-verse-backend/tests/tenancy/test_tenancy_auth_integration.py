"""Real-Postgres regression tests (migrated schema, NOBYPASSRLS app role).

* IP allowlist: a CIDR added through ``POST /tenants/me/ip-allowlist`` is
  actually enforced on the next request (it used to land in the un-enforced
  legacy ``ip_allowlist`` table, and the enforcement cache was never cleared).
* Legal holds: ``POST /governance/legal-hold`` persists a row in the real
  ``legal_holds`` schema (it used to INSERT a non-existent ``reason`` column) and
  the tenant-wide hold blocks deletion checks.
* SCIM: ``SCIMHandler`` CRUD works against the real ``users`` +
  ``tenant_memberships`` schema (it targeted ``users`` columns that never existed).

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/tenancy/test_tenancy_auth_integration.py -q -m integration --no-cov
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import fakeredis
import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TENANT_A = "tenant-int-a"
TENANT_B = "tenant-int-b"
_KEY = "ak_integration"
_TABLES = ("ip_allowlist_entries", "legal_holds", "users", "tenant_memberships")


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


@pytest_asyncio.fixture(scope="function")
async def factories(postgres_url: str) -> AsyncIterator[tuple[Any, Any]]:
    password = secrets.token_urlsafe(24)
    role = f"test_app_tenancy_{secrets.token_hex(4)}"
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
        for tbl in _TABLES:
            await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO {role}"))
            await conn.execute(text(f"DELETE FROM {tbl}"))
    app_url = (
        make_url(postgres_url)
        .set(username=role, password=password)
        .render_as_string(hide_password=False)
    )
    app_engine = create_async_engine(app_url, pool_size=4, max_overflow=0)
    yield (
        async_sessionmaker(admin_engine, expire_on_commit=False),
        async_sessionmaker(app_engine, expire_on_commit=False),
    )
    await app_engine.dispose()
    await admin_engine.dispose()


def _app(app_factory: Any) -> FastAPI:
    from app.api.governance import router as governance_router
    from app.api.tenants import router as tenants_router
    from app.auth.scope_enforcement import ScopeEnforcementMiddleware
    from app.tenancy.context import PlanTier, TenantContext
    from app.tenancy.middleware import TenantMiddleware

    ctx = TenantContext(
        tenant_id=TENANT_A, plan=PlanTier.ENTERPRISE, api_key_id="kid-a", roles=("admin",)
    )

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == _KEY else None

    app = FastAPI()
    app.add_middleware(ScopeEnforcementMiddleware)
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(tenants_router)
    app.include_router(governance_router)

    @app.get("/goals")
    async def goals() -> dict[str, str]:
        return {"ok": "yes"}

    app.state.db_session_factory = app_factory
    app.state.tenant_service = SimpleNamespace(_db=app_factory)
    app.state._rate_limiter_redis = fakeredis.FakeAsyncRedis()
    return app


# ── IP allowlist ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_added_cidr_is_enforced_on_the_next_request(factories: tuple) -> None:
    admin_factory, app_factory = factories
    app = _app(app_factory)
    h = {"X-API-Key": _KEY}
    # httpx on the test's own loop: TestClient runs the app on a separate loop,
    # which the asyncpg pool (bound to this loop) cannot be used from.
    outside = AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.9", 5000)), base_url="http://t"
    )
    inside = AsyncClient(
        transport=ASGITransport(app=app, client=("10.1.2.3", 5000)), base_url="http://t"
    )

    # No allowlist yet → allowed (and the enforcement cache now holds "[]").
    assert (await outside.get("/goals", headers=h)).status_code == 200

    resp = await inside.post("/tenants/me/ip-allowlist", json={"cidr": "10.0.0.0/8"}, headers=h)
    assert resp.status_code == 201, resp.text
    entry_id = resp.json()["id"]

    async with admin_factory() as s:
        rows = (
            await s.execute(
                text("SELECT cidr, is_active FROM ip_allowlist_entries WHERE tenant_id = :t"),
                {"t": TENANT_A},
            )
        ).fetchall()
    assert [tuple(r) for r in rows] == [("10.0.0.0/8", True)]

    # Enforced immediately (cache invalidated), not after the 60 s TTL.
    blocked = await outside.get("/goals", headers=h)
    assert blocked.status_code == 403
    assert blocked.json()["error"] == "IP_NOT_ALLOWED"
    assert (await inside.get("/goals", headers=h)).status_code == 200
    listed = (await inside.get("/tenants/me/ip-allowlist", headers=h)).json()
    assert [e["cidr"] for e in listed] == ["10.0.0.0/8"]

    assert (
        await inside.delete(f"/tenants/me/ip-allowlist/{entry_id}", headers=h)
    ).status_code == 204
    assert (await outside.get("/goals", headers=h)).status_code == 200


# ── Legal holds ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_legal_hold_api_persists_and_blocks_deletion(factories: tuple) -> None:
    from app.governance.legal_holds import LegalHoldManager

    admin_factory, app_factory = factories
    client = AsyncClient(
        transport=ASGITransport(app=_app(app_factory), client=("127.0.0.1", 5000)),
        base_url="http://t",
    )
    h = {"X-API-Key": _KEY}

    resp = await client.post("/governance/legal-hold", json={"reason": "SEC inquiry"}, headers=h)
    assert resp.status_code == 200, resp.text

    async with admin_factory() as s:
        row = (
            await s.execute(
                text(
                    "SELECT name, description, resource_type, status FROM legal_holds "
                    "WHERE tenant_id = :t"
                ),
                {"t": TENANT_A},
            )
        ).one()
    assert tuple(row) == ("SEC inquiry", "SEC inquiry", "tenant", "active")

    listed = await client.get("/governance/legal-holds", headers=h)
    assert listed.status_code == 200
    assert [x["reason"] for x in listed.json()] == ["SEC inquiry"]

    mgr = LegalHoldManager(redis=None, db_factory=app_factory)
    assert await mgr.is_under_hold(TENANT_A, "any-collection") is True
    assert await mgr.is_under_hold(TENANT_B, "any-collection") is False


# ── SCIM ──────────────────────────────────────────────────────────────────────


def _scim(tenant_id: str, app_factory: Any) -> Any:
    from app.auth.scim_handler import SCIMHandler

    return SCIMHandler(
        tenant_id=tenant_id,
        config={
            "allow_user_create": True,
            "allow_user_update": True,
            "allow_user_delete": True,
            "default_role": "viewer",
            "group_role_map": {"Admins": "admin"},
        },
        db_factory=app_factory,
    )


@pytest.mark.asyncio
async def test_scim_user_lifecycle_against_real_schema(factories: tuple) -> None:
    _admin, app_factory = factories
    a = _scim(TENANT_A, app_factory)

    created = await a.create_user(
        {
            "userName": "jane@corp.test",
            "name": {"givenName": "Jane", "familyName": "Doe"},
            "groups": [{"display": "Admins"}],
        }
    )
    uid = created["id"]
    assert created["userName"] == "jane@corp.test"
    assert created["displayName"] == "Jane Doe"
    assert created["active"] is True
    assert created["roles"] == [{"value": "admin"}]

    again = await a.create_user({"userName": "jane@corp.test"})  # idempotent
    assert again["id"] == uid

    listed = await a.list_users()
    assert listed["totalResults"] == 1
    assert [r["id"] for r in listed["Resources"]] == [uid]
    assert (await a.get_user(uid))["userName"] == "jane@corp.test"
    assert (await a.get_user("jane@corp.test"))["id"] == uid

    patched = await a.update_user(
        uid,
        {"Operations": [{"op": "replace", "path": "active", "value": False}]},
        partial=True,
    )
    assert patched["active"] is False
    put = await a.update_user(uid, {"active": True, "name": {"givenName": "X"}})
    assert put["active"] is True
    assert put["displayName"] == "Jane Doe"  # global identity not renamed

    await a.delete_user(uid)
    assert (await a.get_user(uid))["active"] is False
    with pytest.raises(HTTPException) as missing:
        await a.delete_user("nobody@corp.test")
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_scim_is_tenant_scoped_and_does_not_rewrite_shared_identity(
    factories: tuple,
) -> None:
    _admin, app_factory = factories
    a = _scim(TENANT_A, app_factory)
    b = _scim(TENANT_B, app_factory)
    created = await a.create_user(
        {"userName": "shared@corp.test", "name": {"givenName": "Real", "familyName": "Name"}}
    )

    assert (await b.list_users())["totalResults"] == 0
    with pytest.raises(HTTPException) as nf:
        await b.get_user(created["id"])
    assert nf.value.status_code == 404

    # Tenant B provisions the same email: it gets its own membership, but may
    # not rename the identity tenant A already knows.
    in_b = await b.create_user(
        {"userName": "shared@corp.test", "name": {"givenName": "Evil", "familyName": "Rename"}}
    )
    assert in_b["id"] == created["id"]
    assert in_b["displayName"] == "Real Name"
    assert (await b.list_users())["totalResults"] == 1
    assert (await a.list_users())["totalResults"] == 1
