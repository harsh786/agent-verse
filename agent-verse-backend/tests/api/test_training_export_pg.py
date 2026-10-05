"""OPS-37 (integration): streamed training export + durable export jobs on real
Postgres under a NOBYPASSRLS application role.

* 1200 qualifying goals export in keyset batches with no duplicates or gaps;
* the per-goal latest-score lookup and the goal walk can use the new indexes;
* a job created through the API is run by the worker function into object
  storage (an in-memory S3 stand-in), then downloaded through the API by the
  owning tenant only.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/api/test_training_export_pg.py -q -m integration
"""

from __future__ import annotations

import asyncio
import io
import json
import secrets
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.tenancy.context import PlanTier, TenantContext
from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = pytest.mark.integration

TENANT_A = "ops37a"
TENANT_B = "ops37b"
N_GOALS = 1200


class _MemStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def upload(self, key: str, fileobj: Any) -> None:
        self.objects[key] = fileobj.read()

    async def open_stream(self, key: str) -> Any:
        return io.BytesIO(self.objects[key])


async def _seed(admin_url: str) -> None:
    eng = create_async_engine(admin_url, poolclass=NullPool)
    async with eng.begin() as c:
        for tid in (TENANT_A, TENANT_B):
            await c.execute(
                text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :e)"),
                {"id": tid, "e": f"{tid}@example.test"},
            )
        # Goals of A: every one completed; scores alternate around the threshold.
        await c.execute(
            text(
                "INSERT INTO goals (id, tenant_id, goal_text, status, created_at) "
                "SELECT 'ga' || lpad(i::text, 6, '0'), :t, 'goal ' || i, 'complete', "
                "NOW() - (i || ' seconds')::interval FROM generate_series(1, :n) i"
            ),
            {"t": TENANT_A, "n": 2 * N_GOALS},
        )
        # Even goals qualify via evaluations (latest wins over an older low score),
        # odd goals have only a low scorecard.
        await c.execute(
            text(
                "INSERT INTO evaluations (id, goal_id, tenant_id, scores, average_score, "
                "passed, strategy_execution_id, created_at) "
                "SELECT md5('old' || i), 'ga' || lpad(i::text, 6, '0'), :t, '{}', 0.1, false, "
                "'se-old', NOW() - interval '1 day' FROM generate_series(2, :n, 2) i"
            ),
            {"t": TENANT_A, "n": 2 * N_GOALS},
        )
        await c.execute(
            text(
                "INSERT INTO evaluations (id, goal_id, tenant_id, scores, average_score, "
                "passed, strategy_execution_id, created_at) "
                "SELECT md5('new' || i), 'ga' || lpad(i::text, 6, '0'), :t, '{}', 0.95, true, "
                "'se-new', NOW() FROM generate_series(2, :n, 2) i"
            ),
            {"t": TENANT_A, "n": 2 * N_GOALS},
        )
        await c.execute(
            text(
                "INSERT INTO goal_steps (id, goal_id, tenant_id, step_index, description, output) "
                "SELECT md5('s' || i), 'ga' || lpad(i::text, 6, '0'), :t, 0, 'd', "
                "repeat('x', 10000) FROM generate_series(1, :n) i"
            ),
            {"t": TENANT_A, "n": 2 * N_GOALS},
        )
        await c.execute(text("ANALYZE goals"))
        await c.execute(text("ANALYZE evaluations"))
    await eng.dispose()


@pytest.fixture(scope="module")
def env() -> Iterator[tuple[Any, str]]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_seed(url))
        engine = loop.run_until_complete(
            app_role_engine(
                url,
                ["goals", "evaluations", "eval_scorecards", "goal_steps",
                 "training_export_jobs"],
            )
        )
        loop.close()
        yield sessionmaker_for(create_async_engine(engine.url, poolclass=NullPool)), url


def _client(db: Any, tenant: str) -> TestClient:
    from app.api.training_export import router

    app = FastAPI()
    app.include_router(router)
    app.state.db_session_factory = db

    @app.middleware("http")
    async def _inject(request: Any, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id=tenant, plan=PlanTier.ENTERPRISE, api_key_id="k"
        )
        return await call_next(request)

    return TestClient(app)


def test_stream_export_is_complete_and_bounded(env: tuple[Any, str]) -> None:
    db, _ = env
    resp = _client(db, TENANT_A).post("/intelligence/export-training-data?limit=10000")
    assert resp.status_code == 200
    lines = [json.loads(x) for x in resp.text.split("\n")]
    goals = [ln["messages"][1]["content"] for ln in lines]
    assert len(goals) == N_GOALS == len(set(goals))  # every even goal once
    assert all(int(g.split()[1]) % 2 == 0 for g in goals)
    # Step outputs are truncated.
    assert all(len(ln["messages"][-1]["content"]) <= 4000 for ln in lines)

    preview = _client(db, TENANT_A).get(
        "/intelligence/export-training-data/preview?limit=10000"
    ).json()
    assert preview["count"] == N_GOALS and len(preview["samples"]) == 3
    # Tenant B sees nothing.
    other = _client(db, TENANT_B).post("/intelligence/export-training-data")
    assert other.status_code == 200 and other.text == ""


def test_score_lookup_and_goal_walk_can_use_the_new_indexes(env: tuple[Any, str]) -> None:
    from app.training_export.stream import CANDIDATES_SQL

    _, url = env

    async def _plan() -> str:
        eng = create_async_engine(url, poolclass=NullPool)
        async with eng.connect() as c:
            await c.execute(text("SET enable_seqscan = off"))
            rows = (
                await c.execute(
                    text(
                        "EXPLAIN " + CANDIDATES_SQL
                        + " ORDER BY g.created_at DESC, g.id DESC LIMIT 200"
                    ),
                    {"tid": TENANT_A, "min_score": 0.8},
                )
            ).fetchall()
        await eng.dispose()
        return "\n".join(r[0] for r in rows)

    plan = asyncio.new_event_loop().run_until_complete(_plan())
    assert "ix_evaluations_goal_created" in plan, plan
    assert "ix_goals_tenant_completed_created" in plan, plan


def test_durable_job_runs_to_object_storage_and_downloads(
    env: tuple[Any, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import training_export as api
    from app.training_export import jobs as export_jobs

    db, _ = env
    store = _MemStore()
    monkeypatch.setattr(export_jobs, "object_store_from_env", lambda: store)
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(api, "_enqueue", lambda j, t: queued.append((j, t)))

    a = _client(db, TENANT_A)
    created = a.post("/intelligence/export-training-data/jobs?format=anthropic&limit=500")
    assert created.status_code == 202, created.text
    job_id = created.json()["job_id"]
    assert queued == [(job_id, TENANT_A)]
    assert a.get(f"/intelligence/export-training-data/jobs/{job_id}/download").status_code == 409

    # The worker (another process in production) runs it.
    result = asyncio.new_event_loop().run_until_complete(
        export_jobs.run_export_job(db, store, job_id, TENANT_A)
    )
    assert result == {"job_id": job_id, "status": "complete", "example_count": 500}
    # A redelivered task does not run it twice.
    again = asyncio.new_event_loop().run_until_complete(
        export_jobs.run_export_job(db, store, job_id, TENANT_A)
    )
    assert again["status"] == "skipped"

    job = a.get(f"/intelligence/export-training-data/jobs/{job_id}").json()
    assert job["status"] == "complete" and job["example_count"] == 500
    body = a.get(job["download_url"])
    assert body.status_code == 200
    assert len(body.text.strip().split("\n")) == 500
    assert [j["job_id"] for j in a.get("/intelligence/export-training-data/jobs").json()[
        "jobs"
    ]] == [job_id]

    b = _client(db, TENANT_B)
    assert b.get(f"/intelligence/export-training-data/jobs/{job_id}").status_code == 404
    assert b.get(f"/intelligence/export-training-data/jobs/{job_id}/download").status_code == 404


def test_failed_job_is_recorded_failed(
    env: tuple[Any, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api import training_export as api
    from app.training_export import jobs as export_jobs

    db, _ = env
    monkeypatch.setattr(export_jobs, "object_store_from_env", lambda: _MemStore())
    monkeypatch.setattr(api, "_enqueue", lambda j, t: None)
    a = _client(db, TENANT_A)
    job_id = a.post("/intelligence/export-training-data/jobs").json()["job_id"]

    class _Broken(_MemStore):
        async def upload(self, key: str, fileobj: Any) -> None:
            raise OSError("bucket gone " + secrets.token_hex(2))

    with pytest.raises(OSError):
        asyncio.new_event_loop().run_until_complete(
            export_jobs.run_export_job(db, _Broken(), job_id, TENANT_A)
        )
    job = a.get(f"/intelligence/export-training-data/jobs/{job_id}").json()
    assert job["status"] == "failed" and "bucket gone" in job["error"]
    assert "download_url" not in job

