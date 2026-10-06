"""Integration: a worker goal's LLM calls reach ``cost_ledger`` as the app role.

Real ``run_goal`` (only the graph's ``run`` body is stubbed, making the same cost
calls a goal makes) against a migrated Postgres testcontainer reached as the
NOSUPERUSER / NOBYPASSRLS application role, and a Redis testcontainer. Before the
fix the worker graph had ``cost_tracker=None`` and no ledger row was written.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/scaling/test_worker_goal_cost_ledger_pg.py -q -m integration
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests._auth_pg import app_role_url
from tests._test_backends import reset_db_singletons
from tests.scaling.test_worker_goal_cost_ledger import (
    GOAL_ID,
    TENANT_ID,
    install_worker_harness,
    run_worker_goal,
)

pytestmark = pytest.mark.integration


async def _ledger_rows(owner_url: str) -> list[tuple[str, str, int, int, float, str]]:
    engine = create_async_engine(owner_url)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT tags->>'role', model, prompt_tokens, completion_tokens, "
                        "       cost_usd, tenant_id "
                        "FROM cost_ledger WHERE goal_id = :g ORDER BY created_at"
                    ),
                    {"g": GOAL_ID},
                )
            ).all()
            return [(r[0], r[1], r[2], r[3], float(r[4]), r[5]) for r in rows]
    finally:
        await engine.dispose()


async def _clear(owner_url: str) -> None:
    engine = create_async_engine(owner_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM cost_ledger WHERE goal_id = :g"), {"g": GOAL_ID})
    finally:
        await engine.dispose()


def test_worker_goal_llm_calls_are_ledgered_as_the_app_role(
    pg_url: str, redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import redis as sync_redis

    import app.scaling.celery_app as celery_app_mod
    import app.scaling.tasks as tasks_mod

    app_url = asyncio.run(app_role_url(pg_url))
    asyncio.run(_clear(pg_url))
    r = sync_redis.Redis.from_url(redis_url, decode_responses=True)
    r.delete(f"cost:goal:{GOAL_ID}", f"cost:goal:{TENANT_ID}:{GOAL_ID}")
    for key in r.keys(f"cost:daily:{TENANT_ID}:*"):
        r.delete(key)

    seen = install_worker_harness(monkeypatch, stub_db=False)
    # The worker process reaches Postgres as the least-privilege app role.
    monkeypatch.setenv("DATABASE_URL", app_url)
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setattr(tasks_mod, "REDIS_URL", redis_url, raising=False)
    monkeypatch.setattr(celery_app_mod, "REDIS_URL", redis_url, raising=False)
    reset_db_singletons()
    try:
        result = run_worker_goal()
    finally:
        reset_db_singletons()

    assert result["status"] == "complete", result
    assert seen["graphs"], "the stubbed graph never ran"

    rows = asyncio.run(_ledger_rows(pg_url))
    assert sorted(row[0] for row in rows) == ["executor", "planner", "verifier"], rows
    for role, model, prompt, completion, cost, tenant in rows:
        assert model == "gpt-4o"
        assert tenant == TENANT_ID
        assert cost > 0, (role, cost)
        assert prompt > 0 and completion > 0
    planner = next(row for row in rows if row[0] == "planner")
    assert (planner[2], planner[3]) == (1000, 500)

    # CostTracker's per-goal counter moved by every ledgered call.
    total = sum(row[4] for row in rows)
    assert float(r.get(f"cost:goal:{GOAL_ID}") or 0) == pytest.approx(total, rel=1e-6)
    # The budget counters (RedisCostController.check_and_record) moved by the calls
    # charged through charge_llm_call — ONCE: the tracker shares the tenant daily
    # key and must not add the same calls to it again.
    charged = sum(row[4] for row in rows if row[0] in ("planner", "verifier"))
    goal_budget = float(r.get(f"cost:goal:{TENANT_ID}:{GOAL_ID}") or 0)
    assert goal_budget == pytest.approx(charged, rel=1e-6)
    daily = r.keys(f"cost:daily:{TENANT_ID}:*")
    assert len(daily) == 1
    assert float(r.get(daily[0]) or 0) == pytest.approx(charged, rel=1e-6)
