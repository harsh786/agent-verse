"""e2e_full (WF-ENGINE-AUDIT): the workflow ENGINE writes its run/step lifecycle
to ``audit_log`` — from the out-of-process Celery worker, tenant-scoped.

WF-AUDIT covered the API actions (created / run_triggered / approval_decided).
The engine's own lifecycle (run started / completed / failed / cancelled,
step started / completed / failed) was never audited: ``AutoAuditMiddleware``
was never instantiated, so ``GET /governance/audit?goal_id=<run_id>`` returned
``[]`` for every run. The rows are now inserted in the same transaction as the
run/step status write, so they cannot be lost by a fire-and-forget task dying
with the worker's event loop.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests.e2e_full._wf_worker import API, create_workflow, poll_run, workflow_worker

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(scope="module")
def celery_worker(app: Any, tmp_path_factory: Any) -> Iterator[dict[str, Any]]:
    with workflow_worker(tmp_path_factory.mktemp("auditworker"), name="wfaudite2e") as w:
        yield w


async def _audit_names(client: Any, run_id: str, want: set[str]) -> list[dict[str, Any]]:
    """Audit rows for the run, polled until every name in ``want`` is present."""
    rows: list[dict[str, Any]] = []
    for _ in range(40):
        resp = await client.get("/governance/audit", params={"goal_id": run_id, "limit": 200})
        assert resp.status_code == 200, resp.text
        rows = resp.json()
        if want <= {r["tool_name"] for r in rows}:
            return rows
        await asyncio.sleep(0.25)
    return rows


async def _trigger(client: Any, workflow_id: str) -> str:
    resp = await client.post(
        f"{API}/workflows/{workflow_id}/trigger", json={"inputs": {}, "dry_run": False}
    )
    assert resp.status_code == 202, resp.text
    return str(resp.json()["run_id"])


async def test_worker_run_and_steps_are_audited_per_tenant(
    app: Any, client: Any, tenant_client: Any, celery_worker: dict[str, Any]
) -> None:
    wid = await create_workflow(
        tenant_client,
        {
            "name": "Audited engine WF",
            "steps": [
                {"id": "first", "type": "transform", "input": {"a": 1}},
                {"id": "second", "type": "transform", "input": {"b": 2}, "depends_on": ["first"]},
            ],
        },
        name=f"engine-audit-{uuid.uuid4().hex[:8]}",
    )
    run_id = await _trigger(tenant_client, wid)
    await poll_run(tenant_client, run_id, {"complete"})

    want = {
        "workflow.run.started",
        "workflow.step.started",
        "workflow.step.completed",
        "workflow.run.completed",
    }
    rows = await _audit_names(tenant_client, run_id, want)
    names = [r["tool_name"] for r in rows]
    assert want <= set(names), f"engine lifecycle not audited: {names}"
    started_steps = {r["step_id"] for r in rows if r["tool_name"] == "workflow.step.started"}
    completed = {r["step_id"] for r in rows if r["tool_name"] == "workflow.step.completed"}
    assert started_steps == {"first", "second"}
    assert completed == {"first", "second"}
    assert names.count("workflow.run.started") == 1

    # Tenant-scoped: another tenant reading the same run id sees nothing.
    from httpx import ASGITransport, AsyncClient

    signup = await client.post(
        "/tenants/signup",
        json={"name": "Other", "email": f"other-{uuid.uuid4().hex[:8]}@example.com"},
    )
    assert signup.status_code == 201, signup.text
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": signup.json()["api_key"]},
    ) as other:
        foreign = await other.get("/governance/audit", params={"goal_id": run_id})
        assert foreign.status_code == 200
        assert foreign.json() == []


async def test_failed_step_and_run_are_audited(
    tenant_client: Any, celery_worker: dict[str, Any]
) -> None:
    wid = await create_workflow(
        tenant_client,
        {
            "name": "Failing engine WF",
            # The SSRF guard refuses loopback: a deterministic step failure.
            "steps": [
                {
                    "id": "call",
                    "type": "http",
                    "url": "http://127.0.0.1:1/x",
                    "on_failure": "abort",
                }
            ],
        },
        name=f"engine-audit-fail-{uuid.uuid4().hex[:8]}",
    )
    run_id = await _trigger(tenant_client, wid)
    await poll_run(tenant_client, run_id, {"failed"})

    want = {"workflow.run.started", "workflow.step.failed", "workflow.run.failed"}
    rows = await _audit_names(tenant_client, run_id, want)
    names = {r["tool_name"] for r in rows}
    assert want <= names, f"failure not audited: {sorted(names)}"
    failed_step = next(r for r in rows if r["tool_name"] == "workflow.step.failed")
    assert failed_step["step_id"] == "call"


async def test_cancel_of_a_parked_run_is_audited(
    tenant_client: Any, celery_worker: dict[str, Any]
) -> None:
    wid = await create_workflow(
        tenant_client,
        {
            "name": "Gate engine WF",
            "steps": [
                {
                    "id": "gate",
                    "type": "hitl",
                    "assignee": {"strategy": "specific", "specific_user": "anonymous"},
                    "actions": [{"id": "approve"}, {"id": "reject"}],
                }
            ],
        },
        name=f"engine-audit-gate-{uuid.uuid4().hex[:8]}",
    )
    run_id = await _trigger(tenant_client, wid)
    await poll_run(tenant_client, run_id, {"waiting_hitl"})
    cancel = await tenant_client.post(f"{API}/runs/{run_id}/cancel")
    assert cancel.status_code in (200, 202), cancel.text
    await poll_run(tenant_client, run_id, {"cancelled"})

    want = {"workflow.run.started", "workflow.run.waiting_approval", "workflow.run.cancelled"}
    rows = await _audit_names(tenant_client, run_id, want)
    names = {r["tool_name"] for r in rows}
    assert want <= names, f"gate/cancel not audited: {sorted(names)}"
