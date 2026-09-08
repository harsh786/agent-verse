"""Session harness for the ``e2e_full`` tier.

This tier proves the *wired* application end-to-end: it boots the real FastAPI
app via ``create_app(manage_pools=True)`` wrapped in
``asgi_lifespan.LifespanManager`` (so the lifespan swap that upgrades in-memory
services to DB/Redis-backed ones actually runs), against a real Postgres and
Redis, with the schema applied by ``alembic upgrade head``. Requests are driven
over an in-process ``httpx.AsyncClient`` (ASGITransport) — no network server, no
Celery worker.

Backends come from either:

* ``E2E_DATABASE_URL`` + ``E2E_REDIS_URL`` env vars (e.g. pointing at
  ``infra/docker-compose.e2e.yml``), or
* ephemeral testcontainers (default) — self-contained, nothing to set up.

Everything is session-scoped and pinned to a single session event loop
(``loop_scope="session"``) because the app's asyncpg/redis pools are bound to the
loop that ran the lifespan; per-test loops would detach them.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

# The app's connection pools are created inside the session-scoped lifespan and
# reused by every test, so all async fixtures and tests must share one loop.
pytestmark = pytest.mark.asyncio(loop_scope="session")

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


# ── Backing services (testcontainers or external compose) ─────────────────────


@pytest.fixture(scope="session")
def _backends() -> Iterator[tuple[str, str]]:
    """Yield ``(database_url, redis_url)`` for the session.

    Prefers externally-provided URLs (``E2E_DATABASE_URL`` / ``E2E_REDIS_URL``)
    so the same suite can run against ``infra/docker-compose.e2e.yml``; otherwise
    spins up ephemeral Postgres (pgvector) + Redis testcontainers.
    """
    ext_db = os.getenv("E2E_DATABASE_URL")
    ext_redis = os.getenv("E2E_REDIS_URL")
    if ext_db and ext_redis:
        yield ext_db, ext_redis
        return

    try:
        from testcontainers.postgres import PostgresContainer
        from testcontainers.redis import RedisContainer
    except ImportError as exc:  # pragma: no cover - dependency guard
        pytest.skip(f"testcontainers unavailable: {exc}")
        return

    try:
        with (
            PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg,
            RedisContainer("redis:7-alpine") as redis,
        ):
            redis_url = (
                f"redis://{redis.get_container_host_ip()}:"
                f"{redis.get_exposed_port(6379)}/0"
            )
            yield pg.get_connection_url(), redis_url
    except Exception as exc:  # pragma: no cover - Docker not available
        pytest.skip(f"could not start e2e_full testcontainers (Docker down?): {exc}")


@pytest.fixture(scope="session")
def _migrated_backends(_backends: tuple[str, str]) -> tuple[str, str]:
    """Apply ``alembic upgrade head`` against the test Postgres, then return URLs.

    Alembic reads the DSN from ``get_settings().database_url``; running it in a
    subprocess with ``DATABASE_URL`` set gives it a clean, uncached Settings and
    keeps its ``asyncio.run`` off the pytest event loop.
    """
    database_url, redis_url = _backends
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "REDIS_URL": redis_url,
        "ENVIRONMENT": "development",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head failed for e2e_full:\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return database_url, redis_url


# ── The booted application ────────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def app(_migrated_backends: tuple[str, str]) -> AsyncIterator[Any]:
    """Boot ``create_app(manage_pools=True)`` with its real lifespan running."""
    from asgi_lifespan import LifespanManager

    database_url, redis_url = _migrated_backends

    # Point the whole process at the test backends and drop the cached Settings so
    # any deep code path that calls get_settings() sees the same URLs the pools use.
    os.environ["DATABASE_URL"] = database_url
    os.environ["REDIS_URL"] = redis_url
    os.environ["ENVIRONMENT"] = "development"

    from app.core.config import Settings, get_settings

    get_settings.cache_clear()

    from app.main import create_app

    settings = Settings(
        database_url=database_url,
        redis_url=redis_url,
        environment="development",
    )
    fastapi_app = create_app(manage_pools=True, settings=settings)

    # Generous startup timeout: the lifespan wires many subsystems (pools,
    # checkpointer, pub/sub, sync_from_db) on first boot.
    async with LifespanManager(fastapi_app, startup_timeout=120, shutdown_timeout=60):
        yield fastapi_app


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def client(app: Any) -> AsyncIterator[Any]:
    """In-process httpx client bound to the booted app."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://e2e-full") as c:
        yield c


# ── Tenant / API-key seeding ──────────────────────────────────────────────────


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def tenant_client(app: Any, client: Any) -> AsyncIterator[Any]:
    """A client whose ``X-API-Key`` header authenticates a seeded tenant.

    Mirrors the seeding used elsewhere (``tests/conftest.py::signed_up_client``):
    POST /tenants/signup returns an ``api_key`` we attach to every request.

    Session-scoped: ``/tenants/signup`` is IP-rate-limited (a real protection),
    so seeding one tenant per test trips a 429 once the suite grows. All e2e
    tests share this tenant and use unique per-test resource names (uuid), so
    isolation assertions (separate collections/triggers/agents) still hold.
    """
    import uuid

    from httpx import ASGITransport, AsyncClient

    email = f"e2e-full-{uuid.uuid4().hex[:12]}@example.com"
    resp = await client.post(
        "/tenants/signup",
        json={"name": "E2E Full", "email": email},
    )
    assert resp.status_code == 201, f"signup failed: {resp.status_code} {resp.text}"
    api_key = resp.json()["api_key"]

    transport = ASGITransport(app=app)  # reuse the booted app
    async with AsyncClient(
        transport=transport,
        base_url="http://e2e-full",
        headers={"X-API-Key": api_key},
    ) as c:
        yield c


# ── Polling helpers ───────────────────────────────────────────────────────────


async def wait_for_status(
    client: Any,
    goal_id: str,
    status: str | set[str],
    *,
    timeout: float = 15.0,
    interval: float = 0.25,
) -> dict[str, Any]:
    """Poll GET /goals/{goal_id} until its status matches, or raise on timeout.

    Returns the final goal payload. ``status`` may be a single value or a set of
    acceptable terminal states.
    """
    wanted = {status} if isinstance(status, str) else set(status)
    deadline = asyncio.get_event_loop().time() + timeout
    last: dict[str, Any] = {}
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/goals/{goal_id}")
        if resp.status_code == 200:
            last = resp.json()
            if str(last.get("status")) in wanted:
                return last
        await asyncio.sleep(interval)
    raise AssertionError(
        f"goal {goal_id} did not reach status {wanted} within {timeout}s; "
        f"last status={last.get('status')!r}"
    )


async def collect_sse(
    client: Any,
    goal_id: str,
    *,
    until: str | None = None,
    timeout: float = 15.0,
    max_events: int = 200,
) -> list[str]:
    """Collect SSE ``data:`` lines from GET /goals/{goal_id}/stream.

    Stops when ``until`` (a substring) is seen in an event, ``max_events`` is
    reached, the stream closes, or ``timeout`` elapses. Best-effort: returns
    whatever was collected.
    """
    events: list[str] = []
    try:
        async with asyncio.timeout(timeout):
            async with client.stream("GET", f"/goals/{goal_id}/stream") as resp:
                if resp.status_code != 200:
                    return events
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:") :].strip()
                    events.append(payload)
                    if until is not None and until in payload:
                        break
                    if len(events) >= max_events:
                        break
    except Exception:
        pass
    return events
