"""Shared testcontainers helpers for the memory package's Postgres tests.

``alembic_upgrade`` migrates a fresh pgvector container; ``app_role_factory``
creates a least-privilege NOBYPASSRLS login role (the production app role's
shape) so FORCE ROW LEVEL SECURITY is actually exercised.
"""

from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def alembic_upgrade(admin_url: str, target: str = "head") -> None:
    result = subprocess.run(
        ["alembic", "upgrade", target],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": admin_url, "ENVIRONMENT": "development"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"alembic failed:\n{result.stderr[-2000:]}"


async def app_role_engine(admin_url: str, tables: list[str]) -> AsyncEngine:
    """Create a NOBYPASSRLS role with DML on *tables*; return an engine for it."""
    password = secrets.token_urlsafe(24)
    role = f"test_app_mem_{secrets.token_hex(4)}"
    admin = create_async_engine(admin_url)
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
        for table in tables:
            await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {role}"))
    await admin.dispose()
    url = make_url(admin_url).set(username=role, password=password)
    return create_async_engine(url.render_as_string(hide_password=False), pool_size=4)


def sessionmaker_for(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
