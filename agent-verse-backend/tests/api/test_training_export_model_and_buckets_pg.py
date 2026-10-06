"""a10-F234-02 / a10-F234-04 (integration, real Postgres, NOBYPASSRLS app role).

* F234-02: the streamed export hard-coded ``"model": "unknown"``. The model now
  comes from the goal's cost attribution (``goal_cost_breakdowns``): the
  executor's model (most calls), else the most-used model of any role, else
  ``"unknown"`` — and never another tenant's row.
* F234-04: ``min_score`` accepts 0.0 but the preview histogram started at 0.80,
  so every score below 0.85 was counted in ``0.80-0.85``.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/api/test_training_export_model_and_buckets_pg.py -q -m integration
"""

from __future__ import annotations

import asyncio
import json
import uuid
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

TENANT_A = uuid.uuid4().hex
TENANT_B = uuid.uuid4().hex

# goal id -> (score, [(role, model, calls), ...]) for tenant A
GOALS: dict[str, tuple[float, list[tuple[str, str, int]]]] = {
    "g-exec": (0.95, [("planner", "planner-model", 9), ("executor", "exec-model", 3)]),
    "g-failover": (0.91, [("executor", "exec-old", 1), ("executor", "exec-new", 4)]),
    "g-planner-only": (0.87, [("planner", "planner-only-model", 2)]),
    "g-none": (0.82, []),
    "g-low": (0.40, [("executor", "low-model", 1)]),
}


async def _seed(admin_url: str) -> None:
    eng = create_async_engine(admin_url, poolclass=NullPool)
    async with eng.begin() as c:
        for tid in (TENANT_A, TENANT_B):
            await c.execute(
                text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :e)"),
                {"id": tid, "e": f"{tid}@example.test"},
            )
        for i, (gid, (score, models)) in enumerate(GOALS.items()):
            await c.execute(
                text(
                    "INSERT INTO goals (id, tenant_id, goal_text, status, created_at) "
                    "VALUES (:id, :t, :txt, 'complete', NOW() - (:i || ' seconds')::interval)"
                ),
                {"id": gid, "t": TENANT_A, "txt": f"goal {gid}", "i": str(i)},
            )
            await c.execute(
                text(
                    "INSERT INTO evaluations (id, goal_id, tenant_id, scores, average_score, "
                    "passed, strategy_execution_id) VALUES (:id, :g, :t, '{}', :s, true, 'se')"
                ),
                {"id": f"ev-{gid}", "g": gid, "t": TENANT_A, "s": score},
            )
            await c.execute(
                text(
                    "INSERT INTO goal_steps (id, goal_id, tenant_id, step_index, description, "
                    "output) VALUES (:id, :g, :t, 0, 'd', 'answer')"
                ),
                {"id": f"st-{gid}", "g": gid, "t": TENANT_A},
            )
            for role, model, calls in models:
                await c.execute(
                    text(
                        "INSERT INTO goal_cost_breakdowns (tenant_id, goal_id, role, model, "
                        "calls) VALUES (CAST(:t AS uuid), :g, :r, :m, :n)"
                    ),
                    {"t": TENANT_A, "g": gid, "r": role, "m": model, "n": calls},
                )
        # Another tenant's breakdown row for the same goal id must never leak in.
        await c.execute(
            text(
                "INSERT INTO goal_cost_breakdowns (tenant_id, goal_id, role, model, calls) "
                "VALUES (CAST(:t AS uuid), 'g-none', 'executor', 'other-tenant-model', 99)"
            ),
            {"t": TENANT_B},
        )
    await eng.dispose()


@pytest.fixture(scope="module")
def db() -> Iterator[Any]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        loop = asyncio.new_event_loop()
        loop.run_until_complete(_seed(url))
        engine = loop.run_until_complete(
            app_role_engine(
                url,
                ["goals", "evaluations", "eval_scorecards", "goal_steps",
                 "goal_cost_breakdowns"],
            )
        )
        loop.close()
        yield sessionmaker_for(create_async_engine(engine.url, poolclass=NullPool))


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


def test_export_reports_the_model_each_goal_ran_on(db: Any) -> None:
    resp = _client(db, TENANT_A).post(
        "/intelligence/export-training-data?format=anthropic&min_score=0.8"
    )
    assert resp.status_code == 200, resp.text
    by_goal = {
        json.loads(line)["messages"][0]["content"]: json.loads(line)["metadata"]["model"]
        for line in resp.text.split("\n")
    }
    assert by_goal == {
        "goal g-exec": "exec-model",  # executor wins over a busier planner
        "goal g-failover": "exec-new",  # most executor calls
        "goal g-planner-only": "planner-only-model",  # no executor row
        "goal g-none": "unknown",  # none of ITS tenant's rows; B's row ignored
    }


def test_preview_histogram_counts_scores_below_080_separately(db: Any) -> None:
    resp = _client(db, TENANT_A).get("/intelligence/export-training-data/preview?min_score=0")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["count"] == 5
    assert data["score_distribution"] == {
        "0.00-0.80": 1,
        "0.80-0.85": 1,
        "0.85-0.90": 1,
        "0.90-0.95": 1,
        "0.95-1.00": 1,
    }
    assert sum(data["score_distribution"].values()) == data["count"]
