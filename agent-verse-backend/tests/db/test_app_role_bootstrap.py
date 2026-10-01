"""LEAST-PRIVILEGE-LOCAL: the idempotent application-role bootstrap.

The local compose stack ran the API and every worker as ``agentverse`` — a
SUPERUSER, so RLS was bypassed on every table and ``GET /runs`` leaked every
tenant's runs. The app now connects as a NOSUPERUSER/NOBYPASSRLS role
(``APP_DB_USER``, default ``agentverse_app``) that ``app.db.app_role`` creates or
repairs after every ``alembic upgrade`` (the migration role keeps ownership).
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.app_role import (
    DEV_DEFAULT_APP_DB_PASSWORD,
    AppRoleError,
    AppRoleSpec,
    app_role_spec_from_env,
    ensure_app_role,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]


# ── spec from env (unit) ──────────────────────────────────────────────────────


def test_spec_is_none_when_no_app_role_configured() -> None:
    assert app_role_spec_from_env({}) is None


def test_spec_requires_a_password() -> None:
    with pytest.raises(AppRoleError):
        app_role_spec_from_env({"APP_DB_USER": "agentverse_app"})


@pytest.mark.parametrize("name", ["Robert'); DROP", "1abc", "a" * 64, "has space", ""])
def test_spec_rejects_unsafe_role_names(name: str) -> None:
    if not name:
        assert app_role_spec_from_env({"APP_DB_USER": name, "APP_DB_PASSWORD": "x"}) is None
        return
    with pytest.raises(AppRoleError):
        app_role_spec_from_env({"APP_DB_USER": name, "APP_DB_PASSWORD": "x"})


def test_production_refuses_the_dev_default_password() -> None:
    env = {
        "APP_DB_USER": "agentverse_app",
        "APP_DB_PASSWORD": DEV_DEFAULT_APP_DB_PASSWORD,
        "ENVIRONMENT": "production",
    }
    with pytest.raises(AppRoleError):
        app_role_spec_from_env(env)
    env["ENVIRONMENT"] = "development"
    assert app_role_spec_from_env(env) == AppRoleSpec(
        role="agentverse_app", password=DEV_DEFAULT_APP_DB_PASSWORD
    )


def test_spec_refuses_to_reuse_the_migration_role() -> None:
    with pytest.raises(AppRoleError):
        app_role_spec_from_env(
            {"APP_DB_USER": "agentverse", "APP_DB_PASSWORD": "x"}, owner_role="agentverse"
        )


# ── against a real Postgres (integration) ─────────────────────────────────────


async def _role_attrs(pg_url: str, role: str) -> Any:
    engine = create_async_engine(pg_url)
    try:
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    text(
                        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolcanlogin "
                        "FROM pg_roles WHERE rolname = :r"
                    ),
                    {"r": role},
                )
            ).one_or_none()
    finally:
        await engine.dispose()


async def _ensure(pg_url: str, spec: AppRoleSpec) -> None:
    engine = create_async_engine(pg_url)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(ensure_app_role, spec)
            await conn.commit()
    finally:
        await engine.dispose()


def _as(pg_url: str, spec: AppRoleSpec) -> str:
    return (
        make_url(pg_url)
        .set(username=spec.role, password=spec.password)
        .render_as_string(hide_password=False)
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bootstrap_creates_a_least_privilege_role_that_rls_binds(pg_url: str) -> None:
    spec = AppRoleSpec(role=f"app_{uuid.uuid4().hex[:8]}", password="pw-1")
    await _ensure(pg_url, spec)
    await _ensure(pg_url, spec)  # idempotent

    attrs = await _role_attrs(pg_url, spec.role)
    assert attrs is not None
    assert (attrs.rolsuper, attrs.rolbypassrls, attrs.rolcreaterole, attrs.rolcreatedb) == (
        False,
        False,
        False,
        False,
    )
    assert attrs.rolcanlogin is True

    owner = create_async_engine(pg_url)
    tid = uuid.uuid4().hex
    async with owner.begin() as conn:
        await conn.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :e)"),
            {"id": tid, "e": f"{tid}@example.test"},
        )
        # A table created AFTER the bootstrap by the migration role (a later
        # migration) is usable by the app role through default privileges.
        await conn.execute(text(f"CREATE TABLE later_{spec.role} (id int)"))
    await owner.dispose()

    app = create_async_engine(_as(pg_url, spec))
    try:
        async with app.connect() as conn:
            # DML on existing tables, and RLS actually binds: no tenant GUC, no rows.
            assert (await conn.execute(text("SELECT count(*) FROM tenants"))).scalar_one() >= 0
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": "x"})
            assert (await conn.execute(text("SELECT count(*) FROM goals"))).scalar_one() == 0
            await conn.execute(text(f"INSERT INTO later_{spec.role} VALUES (1)"))
            await conn.rollback()
        async with app.connect() as conn:
            with pytest.raises(Exception, match="permission denied"):
                await conn.execute(text(f"CREATE TABLE evil_{spec.role} (id int)"))
    finally:
        await app.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_bootstrap_repairs_an_existing_privileged_role(pg_url: str) -> None:
    spec = AppRoleSpec(role=f"app_{uuid.uuid4().hex[:8]}", password="new-pw")
    owner = create_async_engine(pg_url)
    async with owner.begin() as conn:
        await conn.execute(text(f"CREATE ROLE {spec.role} LOGIN PASSWORD 'old' BYPASSRLS"))
    await owner.dispose()

    await _ensure(pg_url, spec)

    attrs = await _role_attrs(pg_url, spec.role)
    assert attrs is not None and attrs.rolbypassrls is False and attrs.rolsuper is False
    app = create_async_engine(_as(pg_url, spec))  # the password was rotated too
    try:
        async with app.connect() as conn:
            assert (await conn.execute(text("SELECT 1"))).scalar_one() == 1
    finally:
        await app.dispose()


@pytest.mark.integration
def test_alembic_migrate_path_runs_the_bootstrap_as_the_migration_role(pg_url: str) -> None:
    """``alembic upgrade head`` migrates via MIGRATION_DATABASE_URL (the owner) and
    then provisions APP_DB_USER — even when DATABASE_URL is the app role itself."""
    role = f"app_{uuid.uuid4().hex[:8]}"
    app_url = (
        make_url(pg_url).set(username=role, password="pw").render_as_string(hide_password=False)
    )
    env = {
        **os.environ,
        "DATABASE_URL": app_url,  # the not-yet-existing app role
        "MIGRATION_DATABASE_URL": pg_url,
        "APP_DB_USER": role,
        "APP_DB_PASSWORD": "pw",
        "ENVIRONMENT": "development",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]

    import asyncio

    attrs = asyncio.run(_role_attrs(pg_url, role))
    assert attrs is not None and attrs.rolbypassrls is False and attrs.rolsuper is False
