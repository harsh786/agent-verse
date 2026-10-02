"""OPS-34 / OPS-04 (integration): tenant custom skills, their version history and
execution history live in Postgres, so every replica sees them.

Two independent FastAPI apps share one Postgres (two replicas) under a
NOBYPASSRLS application role, so FORCE ROW LEVEL SECURITY is exercised.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/api/test_skills_runtime_store_pg.py -q -m integration
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

KEY_A = "av_test_ops34_a"
KEY_B = "av_test_ops34_b"
TENANT_A = "ops34-tenant-a"
TENANT_B = "ops34-tenant-b"
HA = {"X-API-Key": KEY_A}
HB = {"X-API-Key": KEY_B}

_TABLES = ["skill_runtime_tenant_state", "skills", "skill_versions", "skill_executions"]


@pytest.fixture(scope="module")
def factory() -> Iterator[Any]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        loop = asyncio.new_event_loop()
        engine = loop.run_until_complete(app_role_engine(url, _TABLES))
        loop.close()
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy.pool import NullPool

        yield sessionmaker_for(create_async_engine(engine.url, poolclass=NullPool))


def _app(factory: Any, provider: Any = None) -> FastAPI:
    from app.api.skills_runtime import router

    app = FastAPI()
    ctxs = {
        KEY_A: TenantContext(tenant_id=TENANT_A, plan=PlanTier.ENTERPRISE, api_key_id="ka"),
        KEY_B: TenantContext(tenant_id=TENANT_B, plan=PlanTier.ENTERPRISE, api_key_id="kb"),
    }

    async def _resolve(key: str) -> TenantContext | None:
        return ctxs.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    app.state.db_session_factory = factory
    app.state._app_provider = provider or FakeProvider(responses=["ran"] * 20)
    return app


def test_skill_created_on_a_is_seen_and_run_on_b_after_b_listed(factory: Any) -> None:
    replica_a = TestClient(_app(factory))
    replica_b = TestClient(_app(factory))

    # Replica B lists first (the old code then cached "loaded" forever).
    assert replica_b.get("/skills-runtime?include_platform=false", headers=HA).json()[
        "skills"
    ] == []

    created = replica_a.post(
        "/skills-runtime",
        headers=HA,
        json={"name": "Cross Replica", "description": "d", "instructions": "x",
              "trigger_hints": ["cross replica magic"]},
    )
    assert created.status_code == 200, created.text
    skill_id = created.json()["skill_id"]

    listed = replica_b.get("/skills-runtime?include_platform=false", headers=HA).json()
    assert [s["skill_id"] for s in listed["skills"]] == [skill_id]

    run = replica_b.post(f"/skills-runtime/{skill_id}/execute", headers=HA,
                         json={"input_context": "go"})
    assert run.status_code == 200, run.text
    assert run.json()["success"] is True

    # An update on B is visible on A.
    upd = replica_b.put(f"/skills-runtime/{skill_id}", headers=HA, json={"name": "Renamed"})
    assert upd.status_code == 200, upd.text
    assert upd.json()["version"] == "1.0.1"
    got = replica_a.get(f"/skills-runtime/{skill_id}", headers=HA).json()
    assert got["name"] == "Renamed"
    assert got["version"] == "1.0.1"

    # Tenant B never sees tenant A's skill.
    assert replica_a.get(f"/skills-runtime/{skill_id}", headers=HB).status_code == 404
    assert replica_a.put(
        f"/skills-runtime/{skill_id}", headers=HB, json={"name": "hijack"}
    ).status_code == 404


def test_history_is_shared_across_replicas_and_tenant_isolated(factory: Any) -> None:
    """OPS-04: execution + version history written on A is read on B (and after a
    restart — B has no process state); tenant B reads none of it."""
    replica_a = TestClient(_app(factory))
    replica_b = TestClient(_app(factory))

    sid = replica_a.post(
        "/skills-runtime", headers=HA, json={"name": "Hist", "description": "d"}
    ).json()["skill_id"]
    for name in ("Hist v2", "Hist v3"):
        assert replica_a.put(f"/skills-runtime/{sid}", headers=HA,
                             json={"name": name}).status_code == 200
    exec_ids = []
    for i in range(3):
        r = replica_a.post(f"/skills-runtime/{sid}/execute", headers=HA,
                           json={"input_context": f"run {i}"})
        assert r.status_code == 200, r.text
        assert r.json()["history_recorded"] is True
        exec_ids.append(r.json()["execution_id"])
    by_id = replica_a.post("/skills-runtime/execute", headers=HA,
                           json={"skill_id": sid, "input_context": "body path"}).json()
    exec_ids.append(by_id["execution_id"])

    versions = replica_b.get(f"/skills-runtime/{sid}/versions", headers=HA).json()
    assert [v["name"] for v in versions["versions"]] == ["Hist v2", "Hist"]
    assert versions["current_version"] == "1.0.2"

    page1 = replica_b.get(f"/skills-runtime/{sid}/executions?limit=3", headers=HA).json()
    assert page1["count"] == 3 and page1["next_cursor"]
    page2 = replica_b.get(f"/skills-runtime/{sid}/executions", headers=HA,
                          params={"limit": 3, "cursor": page1["next_cursor"]}).json()
    got = [e["execution_id"] for e in page1["executions"] + page2["executions"]]
    assert sorted(got) == sorted(exec_ids)
    assert page2["next_cursor"] is None

    # Tenant B: the skill is 404 and the execution history is empty under RLS.
    assert replica_b.get(f"/skills-runtime/{sid}/versions", headers=HB).status_code == 404
    assert replica_b.get(f"/skills-runtime/{sid}/executions", headers=HB).json()[
        "executions"
    ] == []
