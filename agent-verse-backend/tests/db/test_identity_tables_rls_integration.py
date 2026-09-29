"""Identity / tenant-config tables under a least-privilege (NOBYPASSRLS) role.

Covers ``saml_configs``, ``scim_configs``, ``scim_tokens``, ``tenant_mfa``,
``tenant_settings``, ``whitelabel_configs``, ``scope_grants`` (tenant policies),
``vault_key_versions`` (platform-global: FORCE RLS + deny-all policy, written only
by the BYPASSRLS maintenance role) and the pre-auth SCIM bearer lookup
(``scim_tokens_by_presented_hash``).

None of these tables had RLS in any migration. ``IDENTITY_RLS_STATEMENTS`` below
is the exact SQL the follow-up migration must apply (idempotent, so this test
still passes once that migration exists and ``alembic upgrade head`` already ran
it). The test then drives the real request-path code — SCIM auth, SCIM token
provisioning, SCIM config load, SAML configure/login, the MFA store, vault key
rotation — as the NOBYPASSRLS role, which is the only configuration under which a
missing ``app.tenant_id`` is visible (a superuser bypasses RLS entirely).

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/db/test_identity_tables_rls_integration.py -q -m integration --no-cov
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import subprocess
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "test_app_identity_rls"
MAINT_ROLE = "test_maint_identity_rls"

TENANT_TABLES = (
    "saml_configs",
    "scim_configs",
    "scim_tokens",
    "tenant_mfa",
    "tenant_settings",
    "whitelabel_configs",
    "scope_grants",
)


def _tenant_policy(table: str) -> list[str]:
    # All seven have a TEXT/VARCHAR tenant_id → compare to the raw GUC.
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {table}_isolation ON {table}",
        f"CREATE POLICY {table}_isolation ON {table} AS PERMISSIVE FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    ]


IDENTITY_RLS_STATEMENTS: list[str] = [
    *(stmt for t in TENANT_TABLES for stmt in _tenant_policy(t)),
    # Pre-auth SCIM bearer lookup (mirrors api_keys_by_presented_hash, b8c9d0e1f2a3).
    "DROP POLICY IF EXISTS scim_tokens_by_presented_hash ON scim_tokens",
    """
    CREATE POLICY scim_tokens_by_presented_hash ON scim_tokens
        AS PERMISSIVE FOR SELECT
        USING (
            COALESCE(current_setting('app.scim_token_hash', true), '') <> ''
            AND token_hash = current_setting('app.scim_token_hash', true)
        )
    """,
    # Platform-global: no tenant can see or write it; only BYPASSRLS roles.
    "ALTER TABLE vault_key_versions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE vault_key_versions FORCE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS vault_key_versions_platform_only ON vault_key_versions",
    "CREATE POLICY vault_key_versions_platform_only ON vault_key_versions "
    "AS PERMISSIVE FOR ALL USING (false) WITH CHECK (false)",
]

# OPTIONAL (no caller yet): narrow pre-auth whitelabel-by-host lookup.
WHITELABEL_FN_STATEMENTS: list[str] = [
    """
    CREATE OR REPLACE FUNCTION app_whitelabel_branding_for_host(p_host text)
    RETURNS TABLE (brand_name text, logo_url text, primary_color text,
                   hide_branding boolean, terms_url text, privacy_url text,
                   support_email text)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = pg_catalog, public
    AS $$
        SELECT w.brand_name, w.logo_url, w.primary_color, w.hide_branding,
               w.terms_url, w.privacy_url, w.support_email
        FROM public.whitelabel_configs w
        WHERE COALESCE(p_host, '') <> ''
          AND lower(w.custom_domain) = lower(p_host)
        LIMIT 1
    $$
    """,
    "REVOKE ALL ON FUNCTION app_whitelabel_branding_for_host(text) FROM PUBLIC",
]


async def _provision(admin_url: str, app_pw: str, maint_pw: str) -> None:
    engine = create_async_engine(admin_url)
    try:
        async with engine.begin() as conn:
            for stmt in IDENTITY_RLS_STATEMENTS + WHITELABEL_FN_STATEMENTS:
                await conn.exec_driver_sql(stmt)
            for role, pw, extra in (
                (APP_ROLE, app_pw, "NOBYPASSRLS"),
                (MAINT_ROLE, maint_pw, "BYPASSRLS"),
            ):
                quoted = (
                    await conn.execute(text("SELECT quote_literal(:p)"), {"p": pw})
                ).scalar_one()
                await conn.exec_driver_sql(
                    f"CREATE ROLE {role} LOGIN PASSWORD {quoted} "
                    f"NOSUPERUSER NOCREATEDB NOCREATEROLE {extra}"
                )
                await conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {role}")
                await conn.exec_driver_sql(
                    f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}"
                )
            for tbl in (*TENANT_TABLES, "vault_key_versions"):
                await conn.exec_driver_sql(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tbl} TO {APP_ROLE}"
                )
            await conn.exec_driver_sql(f"GRANT SELECT ON tenants TO {APP_ROLE}")
            await conn.exec_driver_sql(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON vault_key_versions TO {MAINT_ROLE}"
            )
            await conn.exec_driver_sql(
                "GRANT EXECUTE ON FUNCTION app_whitelabel_branding_for_host(text) "
                f"TO {APP_ROLE}"
            )
    finally:
        await engine.dispose()


def _role_url(admin_url: str, role: str, pw: str) -> str:
    return make_url(admin_url).set(username=role, password=pw).render_as_string(
        hide_password=False
    )


@pytest.fixture(scope="module")
def pg_urls() -> Iterator[dict[str, str]]:
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
        app_pw, maint_pw = secrets.token_urlsafe(18), secrets.token_urlsafe(18)
        asyncio.run(_provision(admin_url, app_pw, maint_pw))
        yield {
            "admin": admin_url,
            "app": _role_url(admin_url, APP_ROLE, app_pw),
            "maint": _role_url(admin_url, MAINT_ROLE, maint_pw),
        }


@pytest_asyncio.fixture
async def dbs(pg_urls: dict[str, str]) -> AsyncIterator[dict[str, Any]]:
    engines = {k: create_async_engine(v, pool_size=2, max_overflow=0) for k, v in pg_urls.items()}
    try:
        yield {k: async_sessionmaker(e, expire_on_commit=False) for k, e in engines.items()}
    finally:
        for e in engines.values():
            await e.dispose()


async def _new_tenants(admin: Any, n: int) -> list[str]:
    ids = [uuid.uuid4().hex for _ in range(n)]
    async with admin() as s, s.begin():
        for tid in ids:
            await s.execute(
                text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :email)"),
                {"id": tid, "email": f"{tid}@example.test"},
            )
    return ids


def _request(db: Any, tenant: str | None, headers: dict[str, str] | None = None) -> Any:
    from app.tenancy.context import PlanTier, TenantContext

    request = MagicMock()
    request.state.tenant = (
        TenantContext(tenant_id=tenant, plan=PlanTier.ENTERPRISE, api_key_id="k", roles=("admin",))
        if tenant
        else None
    )
    request.app.state.db_session_factory = db
    request.app.state.redis = None
    request.base_url = "http://testserver/"
    request.headers = headers or {}
    return request


# ── Catalog ───────────────────────────────────────────────────────────────────


async def test_every_identity_table_has_forced_rls_and_a_policy(dbs: dict[str, Any]) -> None:
    async with dbs["admin"]() as s:
        rows = (
            await s.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                    "(SELECT count(*) FROM pg_policy p WHERE p.polrelid = c.oid) "
                    "FROM pg_class c WHERE c.relname = ANY(:names)"
                ),
                {"names": [*TENANT_TABLES, "vault_key_versions"]},
            )
        ).fetchall()
    by_name = {r[0]: r[1:] for r in rows}
    assert set(by_name) == {*TENANT_TABLES, "vault_key_versions"}
    for name, (enabled, forced, policies) in by_name.items():
        assert enabled and forced and policies >= 1, (name, enabled, forced, policies)


# ── Generic tenant isolation, per table ───────────────────────────────────────

_INSERT = {
    "saml_configs": "INSERT INTO saml_configs (tenant_id, idp_entity_id, idp_sso_url, idp_cert, "
    "sp_entity_id) VALUES (:t, 'e', 'https://idp/sso', 'c', 's')",
    "scim_configs": "INSERT INTO scim_configs (tenant_id, bearer_token_hash, bearer_token_prefix) "
    "VALUES (:t, :u, 'p')",
    "scim_tokens": "INSERT INTO scim_tokens (tenant_id, token_hash) VALUES (:t, :u)",
    "tenant_mfa": "INSERT INTO tenant_mfa (tenant_id) VALUES (:t)",
    "tenant_settings": "INSERT INTO tenant_settings (tenant_id) VALUES (:t)",
    "whitelabel_configs": "INSERT INTO whitelabel_configs (tenant_id) VALUES (:t)",
    "scope_grants": "INSERT INTO scope_grants (tenant_id, grantor_id, grantee_id, scope) "
    "VALUES (:t, 'g1', 'g2', 'goals:read')",
}


async def _set_tenant(session: Any, tenant: str) -> None:
    await session.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_tenant_policy_isolates_reads_and_writes(dbs: dict[str, Any], table: str) -> None:
    admin, app = dbs["admin"], dbs["app"]
    a, b, c = await _new_tenants(admin, 3)
    async with admin() as s, s.begin():
        for t in (a, b):
            await s.execute(text(_INSERT[table]), {"t": t, "u": uuid.uuid4().hex})

    # No GUC → nothing visible (fail closed, not "all rows").
    async with app() as s, s.begin():
        assert (await s.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0

    # Tenant A sees exactly its own rows.
    async with app() as s, s.begin():
        await _set_tenant(s, a)
        seen = (await s.execute(text(f"SELECT DISTINCT tenant_id FROM {table}"))).scalars().all()
        assert seen == [a]

    # A can write its own row (the request-path shape) ...
    async with app() as s, s.begin():
        await _set_tenant(s, a)
        await s.execute(
            text(f"UPDATE {table} SET tenant_id = tenant_id WHERE tenant_id = :t"), {"t": a}
        )

    # ... but cannot insert a row for another tenant ...
    with pytest.raises(Exception, match="row-level security"):
        async with app() as s, s.begin():
            await _set_tenant(s, a)
            await s.execute(text(_INSERT[table]), {"t": c, "u": uuid.uuid4().hex})

    # ... nor move its row to another tenant ...
    with pytest.raises(Exception, match="row-level security"):
        async with app() as s, s.begin():
            await _set_tenant(s, a)
            await s.execute(
                text(f"UPDATE {table} SET tenant_id = :c WHERE tenant_id = :a"), {"a": a, "c": c}
            )

    # ... nor touch B's row.
    async with app() as s, s.begin():
        await _set_tenant(s, a)
        res = await s.execute(text(f"DELETE FROM {table} WHERE tenant_id = :b"), {"b": b})
        assert res.rowcount == 0


# ── SCIM: pre-auth bearer lookup + tenant-scoped provisioning/config ─────────


async def test_scim_token_provision_and_bearer_auth_under_least_privilege(
    dbs: dict[str, Any],
) -> None:
    from app.api.enterprise import _get_scim_handler, provision_scim_token
    from app.auth.scim_handler import require_scim_auth

    admin, app = dbs["admin"], dbs["app"]
    a, b = await _new_tenants(admin, 2)

    # Provisioning (authenticated as A) inserts under A's GUC.
    tok_a = (await provision_scim_token(_request(app, a)))["token"]
    tok_b = (await provision_scim_token(_request(app, b)))["token"]
    async with admin() as s:
        owners = dict(
            (await s.execute(text("SELECT token_hash, tenant_id FROM scim_tokens"))).fetchall()
        )
    assert owners[hashlib.sha256(tok_a.encode()).hexdigest()] == a

    # Pre-auth lookup resolves the right tenant, with no tenant GUC available.
    bearer = {"Authorization": f"Bearer {tok_a}"}
    assert await require_scim_auth(_request(app, None, bearer)) == a
    assert await require_scim_auth(_request(app, None, {"Authorization": f"Bearer {tok_b}"})) == b
    with pytest.raises(HTTPException) as exc:
        await require_scim_auth(_request(app, None, {"Authorization": "Bearer nope"}))
    assert exc.value.status_code == 401

    # The hash GUC exposes exactly the presented token's row — and is read-only.
    async with app() as s, s.begin():
        await s.execute(
            text("SELECT set_config('app.scim_token_hash', :h, true)"),
            {"h": hashlib.sha256(tok_a.encode()).hexdigest()},
        )
        assert (await s.execute(text("SELECT tenant_id FROM scim_tokens"))).scalars().all() == [a]
        res = await s.execute(text("UPDATE scim_tokens SET revoked_at = NOW()"))
        assert res.rowcount == 0

    # A revoked token no longer authenticates.
    async with admin() as s, s.begin():
        await s.execute(
            text("UPDATE scim_tokens SET revoked_at = NOW() WHERE tenant_id = :t"), {"t": b}
        )
    with pytest.raises(HTTPException) as exc:
        await require_scim_auth(_request(app, None, {"Authorization": f"Bearer {tok_b}"}))
    assert exc.value.status_code == 401

    # The tenant's SCIM restrictions load under its GUC (without it, the row is
    # invisible and the handler silently falls back to permissive defaults).
    async with admin() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO scim_configs (tenant_id, bearer_token_hash, bearer_token_prefix, "
                "allow_user_create, group_role_map) "
                "VALUES (:t, 'h', 'p', FALSE, CAST(:m AS jsonb))"
            ),
            {"t": a, "m": '{"Ops": "operator"}'},
        )
    handler = await _get_scim_handler(_request(app, None, bearer))
    assert handler._tenant_id == a
    assert handler._config["allow_user_create"] is False
    assert handler._config["group_role_map"] == {"Ops": "operator"}


# ── SAML ──────────────────────────────────────────────────────────────────────


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("onelogin") is None,
    reason="python3-saml (the optional 'saml' extra, needs native libxmlsec1) is not installed",
)
async def test_saml_configure_and_login_under_least_privilege(dbs: dict[str, Any]) -> None:
    from app.api.enterprise import SAMLConfigRequest, configure_saml, saml_login

    admin, app = dbs["admin"], dbs["app"]
    a, b = await _new_tenants(admin, 2)
    body = SAMLConfigRequest(
        idp_entity_id="idp-a",
        idp_sso_url="https://idp-a.example/sso",
        idp_cert="CERT",
        sp_entity_id="sp-a",
    )
    assert (await configure_saml(_request(app, a), body))["status"] == "configured"
    # Upsert path (ON CONFLICT DO UPDATE) also passes the policy.
    body.idp_sso_url = "https://idp-a.example/sso2"
    await configure_saml(_request(app, a), body)

    resp = await saml_login(_request(app, a))
    assert resp.headers["location"] == "https://idp-a.example/sso2"

    with pytest.raises(HTTPException) as exc:
        await saml_login(_request(app, b))
    assert exc.value.status_code == 404

    async with admin() as s:
        rows = (await s.execute(text("SELECT tenant_id FROM saml_configs"))).scalars().all()
    assert a in rows and b not in rows


# ── MFA ───────────────────────────────────────────────────────────────────────


async def test_mfa_state_persists_and_is_enforceable_across_replicas(dbs: dict[str, Any]) -> None:
    from app.api.mfa import MFAStore

    admin, app = dbs["admin"], dbs["app"]
    a, b = await _new_tenants(admin, 2)

    writer = MFAStore()
    writer.set_db(app)
    await writer.save(
        a,
        {
            "enabled": True,
            "secret": "JBSWY3DPEHPK3PXP",
            "pending_secret": None,
            "recovery_codes_hashed": ["h1", "h2"],
        },
    )
    async with admin() as s:
        row = (
            await s.execute(text("SELECT enabled FROM tenant_mfa WHERE tenant_id = :t"), {"t": a})
        ).scalar_one()
    assert row is True, "MFA enrollment did not persist under the least-privilege role"

    # A different replica (empty cache) must still see MFA as enabled — this is
    # what the enforcement middleware reads.
    reader = MFAStore()
    reader.set_db(app)
    state = await reader.get(a)
    assert state["enabled"] is True
    assert state["secret"] == "JBSWY3DPEHPK3PXP"
    assert state["recovery_codes_hashed"] == ["h1", "h2"]
    assert (await reader.get(b))["enabled"] is False

    # Update path (existing row) under the GUC too.
    await writer.save(
        a, {"enabled": False, "secret": None, "pending_secret": None, "recovery_codes_hashed": []}
    )
    fresh = MFAStore()
    fresh.set_db(app)
    assert (await fresh.get(a))["enabled"] is False


# ── vault_key_versions: platform-global ───────────────────────────────────────


async def test_vault_key_versions_is_maintenance_only(dbs: dict[str, Any]) -> None:
    from app.providers.vault import CredentialVault

    admin, app, maint = dbs["admin"], dbs["app"], dbs["maint"]

    async def _count() -> int:
        async with admin() as s:
            return int((await s.execute(text("SELECT count(*) FROM vault_key_versions"))).scalar())

    before = await _count()
    vault = CredentialVault(master_key="k" * 32)
    await vault.rotate_key(b"r" * 32, db=maint)
    assert await _count() == before + 1

    # The API role can neither see nor write it, even with a tenant GUC set.
    async with app() as s, s.begin():
        await _set_tenant(s, "global")
        assert (await s.execute(text("SELECT count(*) FROM vault_key_versions"))).scalar() == 0
    await vault.rotate_key(b"s" * 32, db=app)  # logged failure, no row
    assert await _count() == before + 1


# ── OPTIONAL whitelabel-by-host lookup ────────────────────────────────────────


async def test_whitelabel_host_lookup_returns_branding_only(dbs: dict[str, Any]) -> None:
    admin, app = dbs["admin"], dbs["app"]
    (a,) = await _new_tenants(admin, 1)
    host = f"{a[:8]}.brand.example"
    async with admin() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO whitelabel_configs (tenant_id, brand_name, custom_domain, "
                "custom_email_from) VALUES (:t, 'Acme', :d, 'secret-from@acme.example')"
            ),
            {"t": a, "d": host},
        )
    async with app() as s, s.begin():
        # No tenant GUC: the table itself is invisible ...
        assert (await s.execute(text("SELECT count(*) FROM whitelabel_configs"))).scalar() == 0
        # ... but the narrow definer function answers the login page's question.
        res = await s.execute(
            text("SELECT * FROM app_whitelabel_branding_for_host(:h)"), {"h": host.upper()}
        )
        row = res.mappings().one()
        assert row["brand_name"] == "Acme"
        assert "custom_email_from" not in row and "tenant_id" not in row
        empty = await s.execute(text("SELECT * FROM app_whitelabel_branding_for_host('')"))
        assert empty.fetchall() == []
