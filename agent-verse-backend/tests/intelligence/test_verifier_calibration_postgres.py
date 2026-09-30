"""Verifier calibration persists and feedback updates it from any process (MEM-32).

record_verdict wrote to columns the table does not have (predicted_success,
verifier_source) and the outcome UPDATE targeted ``actual_success`` — every
write failed and was logged at debug. Goal feedback found the record by
scanning this process's in-memory buffer, so feedback for a goal verified on
another replica or a worker never reached the row.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]


async def test_feedback_from_another_process_updates_the_persisted_verdict() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from testcontainers.postgres import PostgresContainer

    from app.intelligence.verifier_calibration import VerifierCalibrationStore

    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as pg:
        admin_url = pg.get_connection_url()
        env = {**os.environ, "DATABASE_URL": admin_url, "ENVIRONMENT": "development"}
        r = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=_BACKEND_ROOT, env=env, capture_output=True, text=True,
        )
        assert r.returncode == 0, f"alembic failed:\n{r.stderr[-1500:]}"

        engine = create_async_engine(admin_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        tenant_id = f"t-{uuid.uuid4().hex[:10]}"
        goal_id = uuid.uuid4().hex

        # The worker that verified the goal (two iterations: replan, then success).
        worker = VerifierCalibrationStore(factory)
        await worker.record_verdict(
            goal_id=goal_id, tenant_id=tenant_id, verifier_verdict=False, iteration=1
        )
        await worker.record_verdict(
            goal_id=goal_id, tenant_id=tenant_id, verifier_verdict=True, iteration=2
        )

        # The API replica receiving the feedback has never seen the goal.
        api = VerifierCalibrationStore(factory)
        updated = await api.record_actual_outcome_by_goal(
            goal_id=goal_id, tenant_id=tenant_id, actual_success=False
        )
        assert updated == 1

        async with factory() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT iteration, verifier_verdict, actual_outcome "
                        "FROM verifier_calibration WHERE goal_id = :g ORDER BY iteration"
                    ),
                    {"g": goal_id},
                )
            ).fetchall()
        # The final verdict (a false confirm) carries the human outcome.
        assert [tuple(r) for r in rows] == [(1, False, None), (2, True, False)]

        # Any replica reports the same false-confirm rate from the table.
        rate = await VerifierCalibrationStore(factory).afalse_confirm_rate(tenant_id)
        assert rate["total"] == 1 and rate["false_positives"] == 1

        # Another tenant's feedback cannot touch the row.
        assert (
            await api.record_actual_outcome_by_goal(
                goal_id=goal_id, tenant_id="t-other", actual_success=True
            )
            == 0
        )
        await engine.dispose()
