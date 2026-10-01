"""e2e_full (WF-40): an approval the worker cannot save fails the gate durably.

Real Postgres + a real Celery worker. ``workflow_approvals`` is made unavailable
(renamed) while a run reaches its approval gate: the worker's write fails, and
the run must stop on that step with an error — not sit in ``waiting_hitl`` on an
approval no reviewer can ever see.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.e2e_full._wf_e2e import create_workflow, gate, poll_run, trigger
from tests.e2e_full.test_workflow_run_e2e import celery_worker  # noqa: F401

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _exec(sql: str) -> None:
    import asyncpg

    conn = await asyncpg.connect(
        os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        await conn.execute(sql)
    finally:
        await conn.close()


async def test_unsaveable_approval_stops_the_run_instead_of_waiting(
    tenant_client: Any,
    celery_worker: dict[str, Any],  # noqa: F811
) -> None:
    wid = await create_workflow(tenant_client, {"name": "gate", "steps": [gate()]})
    await _exec("ALTER TABLE workflow_approvals RENAME TO workflow_approvals_off")
    try:
        run_id = await trigger(tenant_client, wid)
        run = await poll_run(tenant_client, run_id)
    finally:
        await _exec("ALTER TABLE workflow_approvals_off RENAME TO workflow_approvals")
    assert run["status"] in {"paused", "failed"}, run
    assert "approval could not be saved" in str(run.get("error") or ""), run
    assert run.get("error_step_id") == "gate", run
