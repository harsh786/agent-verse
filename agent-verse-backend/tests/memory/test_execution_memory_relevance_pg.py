"""MEM-36 (integration): an old relevant winning plan / failure is recalled for
an active tenant, and the trigram index serves the query.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/memory/test_execution_memory_relevance_pg.py -q -m integration
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.memory.execution import ExecutionMemory
from tests.memory._pg import alembic_upgrade, app_role_engine, sessionmaker_for

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

TENANT = "mem36-tenant"


@pytest.fixture(scope="module")
def admin_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        url = pg.get_connection_url()
        alembic_upgrade(url)
        yield url


@pytest.fixture(scope="module")
async def sessions(admin_url: str) -> Any:
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        await c.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:t, :t, :e)"),
            {"t": TENANT, "e": f"{TENANT}@example.test"},
        )
        insert = text(
            "INSERT INTO execution_memory (id, tenant_id, goal_text, plan, success, created_at) "
            "VALUES (:id, :t, :g, CAST(:p AS jsonb), :s, now() - (:age * interval '1 day'))"
        )
        # The relevant rows are the OLDEST; 300 newer unrelated rows of each kind.
        await c.execute(insert, {"id": "rel-ok", "t": TENANT, "g": "rotate the TLS certificates",
                                 "p": json.dumps(["renew", "deploy"]), "s": True, "age": 400})
        await c.execute(insert, {"id": "rel-ko", "t": TENANT, "g": "rotate the TLS certificates",
                                 "p": json.dumps({"error": "expired root"}), "s": False,
                                 "age": 400})
        for i in range(300):
            for ok in (True, False):
                await c.execute(insert, {
                    "id": f"n{i}{ok}", "t": TENANT, "g": f"summarise sales region {i}",
                    "p": json.dumps(["query"] if ok else {"error": "x"}), "s": ok, "age": 1,
                })
    await eng.dispose()
    app_eng = await app_role_engine(admin_url, ["execution_memory"])
    yield sessionmaker_for(app_eng)
    await app_eng.dispose()


async def test_old_relevant_plan_and_failure_are_recalled(sessions: Any) -> None:
    mem = ExecutionMemory()
    plans = await mem.recall_async("rotate TLS certificates", tenant_id=TENANT, db=sessions)
    assert not plans.degraded
    assert plans[0]["goal"] == "rotate the TLS certificates"
    assert plans[0]["plan"] == ["renew", "deploy"]
    fails = await mem.recall_failures_async("rotate TLS certificates", tenant_id=TENANT,
                                            db=sessions)
    assert fails[0]["error"] == "expired root"


async def _explain(admin_url: str, sql: str, params: dict[str, Any]) -> str:
    eng = create_async_engine(admin_url)
    async with eng.begin() as c:
        await c.execute(text("SET LOCAL enable_seqscan = off"))
        rows = (await c.execute(text(f"EXPLAIN {sql}"), params)).fetchall()
    await eng.dispose()
    return "\n".join(r[0] for r in rows)


async def test_indexes_serve_the_recall_queries(admin_url: str) -> None:
    recall = await _explain(
        admin_url,
        "SELECT goal_text FROM execution_memory WHERE tenant_id = :t AND success = TRUE "
        "AND (goal_text % :q OR :q <% goal_text)",
        {"t": TENANT, "q": "rotate TLS"},
    )
    assert "Seq Scan" not in recall
    # The trigram predicate on its own is served by the GIN trigram index.
    trgm = await _explain(
        admin_url,
        "SELECT goal_text FROM execution_memory WHERE goal_text % :q OR :q <% goal_text",
        {"q": "rotate TLS"},
    )
    assert "ix_execution_memory_goal_text_trgm" in trgm
