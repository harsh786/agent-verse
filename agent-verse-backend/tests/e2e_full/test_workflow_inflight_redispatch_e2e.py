"""e2e_full (WF-14 / WF-48): a run whose worker died mid-step is resumed safely.

Real Postgres + a real Celery worker. The database is put in the state a killed
worker leaves behind — run ``running`` an hour ago, ``prep`` COMPLETE, ``send``
(an emit_event) still ``running`` — and the stuck-run sweep is run in the worker.
It re-dispatches the run: ``prep`` is not executed again, ``send`` runs as a new
attempt and carries its deterministic idempotency key, and the run completes.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from tests.e2e_full._wf_e2e import API, create_workflow, poll_run
from tests.e2e_full.test_workflow_run_e2e import celery_worker  # noqa: F401

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _db() -> Any:
    import asyncpg

    return await asyncpg.connect(
        os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    )


async def test_stuck_run_redispatch_replays_only_the_inflight_step(
    tenant_client: Any,
    celery_worker: dict[str, Any],  # noqa: F811
) -> None:
    wid = await create_workflow(
        tenant_client,
        {
            "name": "crash mid-step",
            "steps": [
                {"id": "prep", "type": "transform", "input": {"x": 1}},
                {"id": "send", "type": "emit_event", "event_channel_out": "orders",
                 "event_payload": {"n": 1}, "depends_on": ["prep"]},
            ],
        },
    )
    run_id = str(uuid.uuid4())
    conn = await _db()
    try:
        tenant_id = await conn.fetchval(
            "SELECT tenant_id FROM workflow_definitions WHERE id = $1::uuid", wid
        )
        await conn.execute(
            "INSERT INTO workflow_runs (id, tenant_id, workflow_id, trigger_type, inputs, "
            " status, started_at, created_at) VALUES ($1::uuid, $2, $3::uuid, 'api', '{}', "
            " 'running', NOW() - interval '1 hour', NOW() - interval '1 hour')",
            run_id, tenant_id, wid,
        )
        await conn.execute(
            "INSERT INTO workflow_step_results (run_id, tenant_id, step_id, step_type, status, "
            " output, attempt_number, started_at, completed_at) VALUES "
            " ($1::uuid, $2, 'prep', 'transform', 'complete', '{\"x\": 1}', 1, "
            "  NOW() - interval '1 hour', NOW() - interval '1 hour'), "
            " ($1::uuid, $2, 'send', 'emit_event', 'running', NULL, 1, "
            "  NOW() - interval '1 hour', NULL)",
            run_id, tenant_id,
        )
    finally:
        await conn.close()

    from app.workflow.celery_tasks import redispatch_stuck_runs

    redispatch_stuck_runs.apply_async(queue="workflows.maintenance")
    done = await poll_run(tenant_client, run_id, {"complete", "failed", "paused"}, timeout=90)
    assert done["status"] == "complete", done

    resp = await tenant_client.get(f"{API}/runs/{run_id}/steps")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["step_id"] for r in rows].count("prep") == 1, rows  # not re-executed
    send_rows = [r for r in rows if r["step_id"] == "send"]
    assert len(send_rows) == 2, send_rows
    final = send_rows[-1]
    assert final["status"] == "complete", final
    assert final["output"]["payload"]["_idempotency_key"] == f"wf:{run_id}:send", final
