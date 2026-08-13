from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.persistence import GoalPersistenceEngine

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://agentverse:agentverse@localhost:5432/agentverse",
)


@pytest.mark.integration
async def test_duplicate_delivery_creates_one_versioned_attempt() -> None:
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tenant_id = f"attempt-{uuid.uuid4().hex[:12]}"
    goal_id = uuid.uuid4().hex
    persistence = GoalPersistenceEngine(db=factory)

    try:
        first = await persistence._write_attempt_start(
            goal_id=goal_id,
            tenant_id=tenant_id,
            attempt_num=1,
            strategy="same_approach",
            enriched_goal="Perform durable work",
            backoff=0,
            strategy_id="plan_execute",
            strategy_version="1.0.0",
            profile_id="profile-1",
            profile_version=2,
            idempotency_key=f"delivery:{tenant_id}:{goal_id}:1",
        )
        replay = await persistence._write_attempt_start(
            goal_id=goal_id,
            tenant_id=tenant_id,
            attempt_num=1,
            strategy="same_approach",
            enriched_goal="Perform durable work",
            backoff=0,
            strategy_id="plan_execute",
            strategy_version="1.0.0",
            profile_id="profile-1",
            profile_version=2,
            idempotency_key=f"delivery:{tenant_id}:{goal_id}:1",
        )
        assert replay == first

        await persistence._write_attempt_end(
            attempt_id=first,
            tenant_id=tenant_id,
            succeeded=True,
            failure_reason="",
            iterations=2,
            cost_usd=0.04,
            checkpoint_reference="checkpoint://final",
            terminal_evidence={"verified": True},
        )

        async with factory() as db, db.begin():
            await db.execute(
                text("SELECT set_config('app.tenant_id', :tenant, true)"),
                {"tenant": tenant_id},
            )
            rows = (
                await db.execute(
                    text(
                        """SELECT strategy_id, strategy_version, profile_id,
                                  profile_version, checkpoint_reference,
                                  terminal_evidence, version
                           FROM goal_attempts
                           WHERE tenant_id=:tenant AND goal_id=:goal"""
                    ),
                    {"tenant": tenant_id, "goal": goal_id},
                )
            ).mappings().all()
        assert len(rows) == 1
        assert rows[0]["strategy_id"] == "plan_execute"
        assert rows[0]["profile_version"] == 2
        assert rows[0]["checkpoint_reference"] == "checkpoint://final"
        assert rows[0]["terminal_evidence"] == {"verified": True}
        assert rows[0]["version"] == 2
    finally:
        async with factory() as db, db.begin():
            await db.execute(text("SET LOCAL row_security = off"))
            await db.execute(
                text("DELETE FROM goal_attempts WHERE tenant_id=:tenant"),
                {"tenant": tenant_id},
            )
        await engine.dispose()
