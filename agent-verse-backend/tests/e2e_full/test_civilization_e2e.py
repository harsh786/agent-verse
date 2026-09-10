"""Full-stack e2e for the civilization tick pipeline against real Postgres + Redis.

Unlike the HTTP-driven e2e_full tests, this one drives the civilization runtime
directly — but against the *same* real backing services (the migrated
testcontainer Postgres and Redis provided by ``conftest._migrated_backends``).
It seeds a civilization with two live members and a pending learning candidate,
then runs a single ``CivilizationOrchestrator.tick()`` and asserts the whole
pipeline did real work end-to-end:

  * breach-check ran (Governor read live metrics from Postgres),
  * a below-floor member was auto-retired (real UPDATE to ``civilization_agents``),
  * an ``agent_retired`` event was persisted to ``civilization_events``,
  * the learning step processed the candidate (real state transition), and
  * society metrics reflect the post-tick roster honestly.

The tick throttle (WS-1) is exercised for real too: the Redis run-lock + a
0-second min-interval guard mean the first tick runs and a second immediately
after is skipped with ``reason="locked"``/``"too_soon"`` semantics.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

pytestmark = [
    pytest.mark.e2e_full,
    pytest.mark.asyncio(loop_scope="session"),
]


@pytest.mark.asyncio(loop_scope="session")
async def test_civilization_tick_e2e(_migrated_backends: tuple[str, str]) -> None:
    database_url, redis_url = _migrated_backends

    import redis.asyncio as aioredis
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.civilization.governor import Governor
    from app.civilization.learning import LearningPipeline
    from app.civilization.models import Constitution
    from app.civilization.orchestrator import CivilizationOrchestrator
    from app.civilization.society import Society

    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    def db() -> object:
        return session_factory()

    redis_client = aioredis.from_url(redis_url, decode_responses=True)

    tenant_id = uuid.uuid4().hex[:24]
    civ_id = uuid.uuid4().hex[:24]
    healthy_agent = uuid.uuid4().hex[:24]
    doomed_agent = uuid.uuid4().hex[:24]
    candidate_id = uuid.uuid4().hex

    # Make the tick throttle deterministic: a real Redis run-lock, but no
    # min-interval window so the first tick is never rejected as "too_soon".
    os.environ["CIV_TICK_MIN_INTERVAL_SECONDS"] = "0"

    constitution = Constitution(
        max_depth=3,
        max_total_agents=10,
        max_concurrent_agents=5,
        total_budget_usd=100.0,
        reputation_floor=0.2,
        idle_ttl_seconds=3600,
        min_viable_roster=1,
    )

    try:
        # ── Seed a civilization, two members, and a learning candidate ─────────
        now = datetime.now(UTC)
        async with session_factory() as s, s.begin():
            await s.execute(
                text(
                    "INSERT INTO civilizations (id, tenant_id, name, status, constitution) "
                    "VALUES (:id, :tid, :name, 'active', CAST(:c AS jsonb))"
                ),
                {"id": civ_id, "tid": tenant_id, "name": "E2E Civ", "c": "{}"},
            )
            # Healthy member: high reputation, recently active → stays.
            await s.execute(
                text(
                    "INSERT INTO civilization_agents "
                    "(id, civilization_id, tenant_id, agent_id, role, reputation, status, "
                    " depth, budget_usd, budget_spent_usd, spawned_at, last_active_at) "
                    "VALUES (:id, :cid, :tid, :aid, 'coordinator', 0.9, 'active', "
                    " 0, 10, 1.5, :now, :now)"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "cid": civ_id,
                    "tid": tenant_id,
                    "aid": healthy_agent,
                    "now": now,
                },
            )
            # Doomed member: reputation below the floor → auto-retired.
            await s.execute(
                text(
                    "INSERT INTO civilization_agents "
                    "(id, civilization_id, tenant_id, agent_id, role, reputation, status, "
                    " depth, budget_usd, budget_spent_usd, spawned_at, last_active_at) "
                    "VALUES (:id, :cid, :tid, :aid, 'worker', 0.05, 'active', "
                    " 1, 5, 0.5, :now, :now)"
                ),
                {
                    "id": uuid.uuid4().hex,
                    "cid": civ_id,
                    "tid": tenant_id,
                    "aid": doomed_agent,
                    "now": now - timedelta(minutes=1),
                },
            )
            # A pending learning candidate for the learning step to process.
            await s.execute(
                text(
                    "INSERT INTO civilization_learnings "
                    "(id, civilization_id, tenant_id, candidate, source_agent_id, status, "
                    " created_at) "
                    "VALUES (:id, :cid, :tid, :cand, :agent, 'candidate', :now)"
                ),
                {
                    "id": candidate_id,
                    "cid": civ_id,
                    "tid": tenant_id,
                    "cand": "Always validate MCP tool schemas before executing a step.",
                    "agent": healthy_agent,
                    "now": now,
                },
            )

        # ── Build the real runtime components ──────────────────────────────────
        society = Society(
            civilization_id=civ_id, tenant_id=tenant_id, db_session_factory=db
        )
        governor = Governor(
            constitution=constitution,
            civilization_id=civ_id,
            tenant_id=tenant_id,
            db_session_factory=db,
            redis=redis_client,
        )
        learning = LearningPipeline(
            civilization_id=civ_id,
            tenant_id=tenant_id,
            db_session_factory=db,
            redis=redis_client,
        )
        orch = CivilizationOrchestrator(
            civilization_id=civ_id,
            tenant_id=tenant_id,
            constitution=constitution,
            governor=governor,
            society=society,
            bus=AsyncMock(),
            blackboard=AsyncMock(),
            learning_pipeline=learning,
            db_session_factory=db,
            redis=redis_client,
        )

        # ── Run one real tick ──────────────────────────────────────────────────
        result = await orch.tick()

        assert result.get("skipped") is not True, result
        # Breach check ran against live DB metrics.
        assert "breach" in result
        assert result["breach"]["detected"] is False
        # The below-floor member was auto-retired; the healthy one kept the roster.
        assert doomed_agent in result.get("auto_retired", [])
        assert healthy_agent not in result.get("auto_retired", [])
        # Reputation sync ran (no seeded evals → honest 0, key present as an int).
        assert isinstance(result.get("reputation_updated"), int)
        # Learning step processed the candidate for real.
        assert "learning" in result
        learn = result["learning"]
        assert learn["validated"] + learn["promoted"] + learn["rejected"] >= 1

        # ── Verify the real DB side effects ────────────────────────────────────
        async with session_factory() as s:
            retired_status = (
                await s.execute(
                    text(
                        "SELECT status FROM civilization_agents "
                        "WHERE agent_id = :aid AND tenant_id = :tid"
                    ),
                    {"aid": doomed_agent, "tid": tenant_id},
                )
            ).scalar_one()
            healthy_status = (
                await s.execute(
                    text(
                        "SELECT status FROM civilization_agents "
                        "WHERE agent_id = :aid AND tenant_id = :tid"
                    ),
                    {"aid": healthy_agent, "tid": tenant_id},
                )
            ).scalar_one()
            retired_event_count = (
                await s.execute(
                    text(
                        "SELECT COUNT(*) FROM civilization_events "
                        "WHERE civilization_id = :cid AND tenant_id = :tid "
                        "AND type = 'agent_retired'"
                    ),
                    {"cid": civ_id, "tid": tenant_id},
                )
            ).scalar_one()
            candidate_status = (
                await s.execute(
                    text("SELECT status FROM civilization_learnings WHERE id = :id"),
                    {"id": candidate_id},
                )
            ).scalar_one()

        assert retired_status == "retired"
        assert healthy_status == "active"
        assert retired_event_count >= 1
        # The candidate left the 'candidate' state (validated/promoted/rejected).
        assert candidate_status in {"validated", "promoted", "rejected"}

        # ── Society metrics reflect the post-tick roster honestly ──────────────
        metrics = await society.get_metrics()
        assert metrics["active_members"] == 1  # only the healthy member remains active
        assert metrics["retired_members"] >= 1  # honest DB count, not silently 0

        # ── The throttle is real: an immediate second tick is skipped ──────────
        os.environ["CIV_TICK_MIN_INTERVAL_SECONDS"] = "60"
        # Re-acquire is blocked because the first tick released the lock but the
        # min-interval window from *this* call is now active on the second call.
        first = await orch.tick()
        second = await orch.tick()
        assert first.get("skipped") is not True
        assert second.get("skipped") is True
        assert second.get("reason") in {"too_soon", "locked"}
    finally:
        os.environ.pop("CIV_TICK_MIN_INTERVAL_SECONDS", None)
        # Best-effort cleanup so reruns against a persistent external DB stay clean.
        try:
            async with session_factory() as s, s.begin():
                await s.execute(
                    text("DELETE FROM civilization_events WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                )
                await s.execute(
                    text("DELETE FROM civilization_learnings WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                )
                await s.execute(
                    text("DELETE FROM civilization_agents WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                )
                await s.execute(
                    text("DELETE FROM civilizations WHERE tenant_id = :tid"),
                    {"tid": tenant_id},
                )
        except Exception:
            pass
        await redis_client.aclose()
        await engine.dispose()
