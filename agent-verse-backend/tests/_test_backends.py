"""Ephemeral Postgres/Redis for tests that genuinely need them (testcontainers).

``tests/conftest.py`` makes the developer's live local Postgres/Redis unreachable
for every test run that does not opt in. Tests that need a real database or Redis
use the session-scoped fixtures built from these helpers instead:

* ``pg_url``    — a pgvector Postgres container with ``alembic upgrade head``
  applied (the same way the ``tests/e2e_full`` harness migrates), as an asyncpg
  DSN for the container's superuser.
* ``redis_url`` — a Redis container.
* ``test_backends`` — points the process (``DATABASE_URL`` / ``REDIS_URL``, the
  cached ``Settings`` and the ``app.db.session`` engine singleton) at those
  containers for one test, for code paths that go through the global engine
  (e.g. ``run_goal``).

Containers start lazily on first use and are shared by the whole session; the
fixtures skip when Docker is unavailable. Tests using them must carry the
``integration`` marker.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PG_IMAGE = "pgvector/pgvector:pg16"
REDIS_IMAGE = "redis:7-alpine"


def alembic_upgrade_head(database_url: str) -> None:
    """Apply every migration to ``database_url`` in a subprocess.

    Alembic reads the DSN from ``get_settings().database_url``; a subprocess with
    ``DATABASE_URL`` set gets a clean, uncached Settings and keeps alembic's
    ``asyncio.run`` off the pytest event loop.
    """
    env = {**os.environ, "DATABASE_URL": database_url, "ENVIRONMENT": "development"}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head failed:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


@contextmanager
def migrated_postgres() -> Iterator[str]:
    """Yield the DSN of a fresh, fully-migrated Postgres container."""
    try:
        from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency guard
        pytest.skip(f"testcontainers unavailable: {exc}")
    try:
        container = PostgresContainer(PG_IMAGE, driver="asyncpg")
        container.start()
    except Exception as exc:  # pragma: no cover - Docker down
        pytest.skip(f"could not start a Postgres testcontainer (Docker down?): {exc}")
    try:
        url = container.get_connection_url()
        alembic_upgrade_head(url)
        yield url
    finally:
        container.stop()


@contextmanager
def fresh_migrated_database(server_url: str) -> Iterator[str]:
    """Yield the DSN of a new, fully-migrated database on ``server_url``'s server.

    For tests whose subject scans a whole store across tenants (e.g. the vault
    master-key rotation), so rows other tests left in the shared ``pg_url``
    database cannot change their result. Dropped afterwards.
    """
    import asyncio
    import uuid
    from concurrent.futures import ThreadPoolExecutor

    import asyncpg

    name = f"isolated_{uuid.uuid4().hex[:12]}"
    raw = server_url.replace("postgresql+asyncpg://", "postgresql://")

    async def _exec(sql: str) -> None:
        conn = await asyncpg.connect(raw)
        try:
            await conn.execute(sql)
        finally:
            await conn.close()

    def _admin(sql: str) -> None:
        # Callers may already be inside an event loop: run on a fresh one in a thread.
        with ThreadPoolExecutor(1) as pool:
            pool.submit(asyncio.run, _exec(sql)).result()

    _admin(f'CREATE DATABASE "{name}"')
    base, _, _db = server_url.rpartition("/")
    url = f"{base}/{name}"
    try:
        alembic_upgrade_head(url)
        yield url
    finally:
        _admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@contextmanager
def redis_container() -> Iterator[str]:
    """Yield the URL of a fresh Redis container."""
    try:
        from testcontainers.redis import RedisContainer  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency guard
        pytest.skip(f"testcontainers unavailable: {exc}")
    try:
        container = RedisContainer(REDIS_IMAGE)
        container.start()
    except Exception as exc:  # pragma: no cover - Docker down
        pytest.skip(f"could not start a Redis testcontainer (Docker down?): {exc}")
    try:
        host = container.get_container_host_ip()
        yield f"redis://{host}:{container.get_exposed_port(6379)}/0"
    finally:
        container.stop()


def reset_db_singletons() -> None:
    """Drop the cached Settings and the lazily-built global engine/factory."""
    from app.core.config import get_settings

    get_settings.cache_clear()
    import app.db.session as db_session

    db_session._engine = None
    db_session._session_factory = None
    for name in ("_system_engine", "_system_session_factory"):
        if hasattr(db_session, name):
            setattr(db_session, name, None)
