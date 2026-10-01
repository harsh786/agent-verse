"""Testcontainers helpers for the knowledge-plane integration tests.

``app_engine`` creates a least-privilege NOBYPASSRLS login role with DML on every
table (the production app role's shape), so FORCE ROW LEVEL SECURITY is
exercised; ``seed_tenant`` inserts a tenant row; ``count`` runs an admin COUNT.
"""

from __future__ import annotations

import secrets
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine


async def app_engine(admin_url: str) -> AsyncEngine:
    password = secrets.token_urlsafe(24)
    role = f"test_app_kb_{secrets.token_hex(4)}"
    admin = create_async_engine(admin_url)
    try:
        async with admin.begin() as conn:
            quoted = (
                await conn.execute(text("SELECT quote_literal(:p)"), {"p": password})
            ).scalar_one()
            await conn.execute(
                text(
                    f"CREATE ROLE {role} LOGIN PASSWORD {quoted} "
                    "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
                )
            )
            db_name = make_url(admin_url).database
            await conn.execute(text(f"GRANT CONNECT ON DATABASE {db_name} TO {role}"))
            await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
            await conn.execute(text(f"GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {role}"))
            await conn.execute(
                text(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"
                )
            )
    finally:
        await admin.dispose()
    url = make_url(admin_url).set(username=role, password=password)
    return create_async_engine(url.render_as_string(hide_password=False), pool_size=4)


def sessions(engine: AsyncEngine) -> async_sessionmaker:  # type: ignore[type-arg]
    return async_sessionmaker(engine, expire_on_commit=False)


async def admin_exec(admin_url: str, sql: str, params: dict[str, Any] | None = None) -> Any:
    """Run one statement as the admin; returns all rows for a SELECT, else None."""
    engine = create_async_engine(admin_url)
    try:
        async with engine.begin() as conn:
            result = await conn.execute(text(sql), params or {})
            return result.fetchall() if result.returns_rows else None
    finally:
        await engine.dispose()


async def seed_tenant(admin_url: str, tenant_id: str) -> None:
    await admin_exec(
        admin_url,
        "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
        "VALUES (:id, :id, :email, 'free', true) ON CONFLICT DO NOTHING",
        {"id": tenant_id, "email": f"{tenant_id}@example.test"},
    )
