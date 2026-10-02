"""Shared Postgres harness for the eval-suite integration tests.

Starts a pgvector testcontainer, migrates it (optionally only up to a given
revision, so a test can seed legacy data before the rest of the upgrade), and
provisions a NOBYPASSRLS application role: the container's default user is a
SUPERUSER, which ignores row-level security, so tenant isolation is only
proven through the app role.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
APP_ROLE = "eval_app_rls"
APP_PASSWORD = "eval-app-rls"


def alembic(url: str, target: str = "head", *, command: str = "upgrade") -> None:
    env = {**os.environ, "DATABASE_URL": url, "ENVIRONMENT": "development"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", command, target],
        cwd=BACKEND_ROOT, env=env, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic {command} {target} failed:\n{result.stderr[-3000:]}")


@dataclass
class EvalPg:
    admin_url: str
    app_url: str


async def provision_app_role(admin_url: str) -> str:
    import asyncpg

    raw = admin_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw)
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", APP_ROLE):
            await conn.execute(
                f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS"
            )
        db = await conn.fetchval("SELECT current_database()")
        await conn.execute(f'GRANT CONNECT ON DATABASE "{db}" TO {APP_ROLE}')
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
        await conn.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}"
        )
        await conn.execute(
            f"GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}"
        )
    finally:
        await conn.close()
    user_part, host_part = admin_url.split("@", 1)
    scheme = user_part.split("://", 1)[0]
    return f"{scheme}://{APP_ROLE}:{APP_PASSWORD}@{host_part}"


@contextmanager
def eval_postgres(upgrade_to: str = "head") -> Iterator[str]:
    """A fresh pgvector container migrated to ``upgrade_to``; yields the admin DSN."""
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError as exc:  # pragma: no cover
        pytest.skip(f"testcontainers unavailable: {exc}")
    try:
        container = PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg")
        container.start()
    except Exception as exc:  # pragma: no cover - Docker down
        pytest.skip(f"could not start a Postgres testcontainer: {exc}")
    try:
        url = container.get_connection_url()
        alembic(url, upgrade_to)
        yield url
    finally:
        container.stop()
