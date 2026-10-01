"""OPS-03 (integration): a tenant-disabled skill never runs — on the direct
execute route, on another replica, or after a restart.

Two independent FastAPI apps share one Postgres (a second replica); the
module-level caches are cleared between calls to model a restart.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/api/test_skills_runtime_disabled_pg.py -q -m integration
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = pytest.mark.integration

KEY = "av_test_ops03"
TENANT = "ops03-tenant"


@pytest.fixture(scope="module")
def factory() -> Iterator[Any]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        loop = asyncio.new_event_loop()
        engine = loop.run_until_complete(
            app_role_engine(url, ["skill_runtime_tenant_state", "skills"])
        )
        loop.close()
        # NullPool: each TestClient runs its own event loop, and pooled asyncpg
        # connections are bound to the loop that opened them.
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        yield sessionmaker_for(create_async_engine(engine.url, poolclass=NullPool))


def _app(factory: Any) -> FastAPI:
    from app.api.skills_runtime import router

    app = FastAPI()
    ctx = TenantContext(tenant_id=TENANT, plan=PlanTier.ENTERPRISE, api_key_id="k")

    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    app.state.db_session_factory = factory
    app.state._app_provider = FakeProvider(responses=["ran"] * 10)
    return app


def _restart() -> None:
    from app.api import skills_runtime
    from app.skills_runtime.executor import permission_checker

    skills_runtime._enabled_skills.clear()
    permission_checker._disabled.clear()


def test_disabled_skill_is_403_on_every_replica_and_after_restart(factory: Any) -> None:
    h = {"X-API-Key": KEY}
    replica_a = TestClient(_app(factory))
    replica_b = TestClient(_app(factory))
    body = {"input_context": "compress this"}

    assert replica_b.post("/skills-runtime/headroom/execute", headers=h, json=body).status_code == 200

    r = replica_a.post("/skills-runtime/permissions/disable", headers=h,
                       json={"skill_id": "headroom"})
    assert r.status_code == 200

    # Another replica (its own process state never saw the disable).
    _restart()
    resp = replica_b.post("/skills-runtime/headroom/execute", headers=h, json=body)
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"].lower()
    # The body-id execute path refuses too (no LLM call, success=False).
    by_id = replica_b.post("/skills-runtime/execute", headers=h,
                           json={"skill_id": "headroom", "input_context": "x"}).json()
    assert by_id["success"] is False
    # Listing reflects the persisted state.
    listed = replica_b.get("/skills-runtime", headers=h).json()["skills"]
    assert next(s for s in listed if s["skill_id"] == "headroom")["enabled"] is False

    # Re-enable through the other API on replica B; replica A (restarted) runs it.
    assert replica_b.post("/skills-runtime/headroom/enable", headers=h).status_code == 200
    _restart()
    assert replica_a.post("/skills-runtime/headroom/execute", headers=h, json=body).status_code == 200
