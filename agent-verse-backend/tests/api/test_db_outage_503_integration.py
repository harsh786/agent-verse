"""A real Postgres outage answers 503 + Retry-After on real endpoints, then recovers.

Uses its OWN migrated Postgres testcontainer (the session-wide ``pg_url`` one is
shared, so it must never be stopped). The container's host port is pinned so a
``docker stop`` / ``docker start`` keeps the DSN — the app's engine is pointed
at the same address before, during and after the outage, like production.

Also covers a DSN whose port is closed from the start (PgBouncer / Postgres not
listening at all).

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/api/test_db_outage_503_integration.py -m integration
"""

from __future__ import annotations

import asyncio
import socket
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.api.goals import router as goals_router
from app.api.governance import router as governance_router
from app.db import session as session_mod
from app.governance.hitl import HITLGateway
from app.main import _register_error_handlers
from app.services.goal_service import GoalService
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from tests._test_backends import PG_IMAGE, alembic_upgrade_head, reset_db_singletons

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="function")]

_KEY = "av_test_db_outage_key"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def outage_pg() -> Iterator[tuple[Any, str]]:
    """(container, asyncpg DSN) of a dedicated, migrated, stoppable Postgres."""
    try:
        from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency guard
        pytest.skip(f"testcontainers unavailable: {exc}")
    container = PostgresContainer(PG_IMAGE, driver="asyncpg").with_bind_ports(5432, _free_port())
    try:
        container.start()
    except Exception as exc:  # pragma: no cover - Docker down
        pytest.skip(f"could not start a Postgres testcontainer (Docker down?): {exc}")
    try:
        url = container.get_connection_url()
        alembic_upgrade_head(url)
        yield container, url
    finally:
        container.stop()


def _raw(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


async def _wait_until_accepting(url: str, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    last: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            conn = await asyncpg.connect(_raw(url), timeout=2)
            await conn.close()
            return
        except Exception as exc:  # starting up / not listening yet
            last = exc
            await asyncio.sleep(0.5)
    raise AssertionError(f"Postgres did not come back within {timeout_s}s: {last!r}")


async def _seed_goal(url: str, tenant_id: str) -> tuple[str, str]:
    """(goal_id, pending approval request id) for a fresh tenant."""
    goal_id = uuid.uuid4().hex
    approval_id = uuid.uuid4().hex
    conn = await asyncpg.connect(_raw(url))
    try:
        await conn.execute(
            "INSERT INTO tenants (id, name, email, plan_tier, is_active) "
            "VALUES ($1, $1, $2, 'free', true) ON CONFLICT DO NOTHING",
            tenant_id,
            f"{tenant_id}@example.test",
        )
        await conn.execute(
            "INSERT INTO goals (id, tenant_id, goal_text, status, priority) "
            "VALUES ($1, $2, 'acknowledged before the outage', 'planning', 'normal')",
            goal_id,
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO approval_requests (id, tenant_id, goal_id, action, risk_level, "
            "status, created_at) VALUES ($1, $2, $3, 'deploy to prod', 'high', 'pending', now())",
            approval_id,
            tenant_id,
            goal_id,
        )
    finally:
        await conn.close()
    return goal_id, approval_id


def _app(database_url: str, tenant_id: str) -> tuple[FastAPI, Any]:
    """Goals + governance APIs: real error handlers, real DB-backed services."""
    ctx = TenantContext(tenant_id=tenant_id, plan=PlanTier.PROFESSIONAL, api_key_id="k-outage")

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == _KEY else None

    factory = session_mod._make_session_factory(database_url)
    app = FastAPI()
    _register_error_handlers(app)
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(goals_router)
    app.include_router(governance_router)
    app.state.goal_service = GoalService(db_session_factory=factory)
    app.state.hitl_gateway = HITLGateway(db_session_factory=factory)
    app.state.db_session_factory = factory
    return app, factory


@pytest_asyncio.fixture
async def client_factory() -> AsyncIterator[Any]:
    engines: list[Any] = []

    def make(database_url: str, tenant_id: str) -> httpx.AsyncClient:
        app, factory = _app(database_url, tenant_id)
        engines.append(factory.kw["bind"])
        # raise_app_exceptions stays True: an outage must be ANSWERED (503), not
        # escape the app as an exception.
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers={"X-API-Key": _KEY},
        )

    yield make
    for engine in engines:
        await engine.dispose()


def _assert_db_503(resp: httpx.Response) -> None:
    assert resp.status_code == 503, resp.text
    assert resp.headers.get("Retry-After", "").isdigit(), resp.headers
    assert int(resp.headers["Retry-After"]) > 0
    err = resp.json()["error"]
    assert err["retryable"] is True
    assert err["code"].endswith("UNAVAILABLE"), err  # e.g. GOAL_STORE_UNAVAILABLE


async def test_postgres_stopped_mid_test_answers_503_then_recovers(
    outage_pg: tuple[Any, str], client_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    container, url = outage_pg
    # The org-gate helpers of the approvals inbox read through the process-global
    # engine: point it at this container too (restored by reset afterwards).
    monkeypatch.setenv("DATABASE_URL", url)
    reset_db_singletons()
    tenant_id = str(uuid.uuid4())
    goal_id, approval_id = await _seed_goal(url, tenant_id)

    async with client_factory(url, tenant_id) as client:
        # Healthy: the acknowledged goal is listed and readable.
        resp = await client.get("/goals")
        assert resp.status_code == 200, resp.text
        assert [g["goal_id"] for g in resp.json()["goals"]] == [goal_id]
        resp = await client.get(f"/goals/{goal_id}")
        assert resp.status_code == 200, resp.text
        resp = await client.get("/governance/approvals")
        assert resp.status_code == 200, resp.text
        assert [a["request_id"] for a in resp.json()] == [approval_id]

        # Outage: stop the Postgres container (pooled connections die with it).
        container.get_wrapped_container().stop(timeout=5)
        try:
            for path in (
                "/goals",
                f"/goals/{goal_id}",
                "/goals?limit=5",
                "/governance/approvals",
                "/governance/hitl/pending",
            ):
                resp = await client.get(path)
                # Never a 500, never a 200 with empty / wrong data.
                _assert_db_503(resp)
            resp = await client.get("/goals")
            assert resp.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
        finally:
            container.get_wrapped_container().start()
            await _wait_until_accepting(url)

        # Recovery on the SAME engine/pool: the acknowledged write survived.
        resp = await client.get("/goals")
        assert resp.status_code == 200, resp.text
        assert [g["goal_id"] for g in resp.json()["goals"]] == [goal_id]
        resp = await client.get(f"/goals/{goal_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["goal"] == "acknowledged before the outage"
        resp = await client.get("/governance/approvals")
        assert resp.status_code == 200, resp.text
        assert [a["request_id"] for a in resp.json()] == [approval_id]
    await session_mod.get_session_factory().kw["bind"].dispose()
    reset_db_singletons()


async def test_closed_port_answers_503(client_factory: Any) -> None:
    closed = f"postgresql+asyncpg://agentverse:agentverse@127.0.0.1:{_free_port()}/agentverse"
    async with client_factory(closed, uuid.uuid4().hex) as client:
        resp = await client.get("/goals")
        _assert_db_503(resp)
        assert resp.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
        resp = await client.get(f"/goals/{uuid.uuid4().hex}")
        _assert_db_503(resp)  # not a 404: the goal's existence is unknown
