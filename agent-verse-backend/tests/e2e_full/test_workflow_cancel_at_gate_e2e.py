"""e2e_full (WF-CANCEL-PAUSED): cancelling a run parked at an approval gate
withdraws its pending approval in the same transaction, and a late approve /
reject on the cancelled run is a 409 that never changes the run's status.

Before: ``cancel_run`` only flipped the run status — the approval stayed
pending in the inbox, and a late approve returned 200, dispatched the resume,
and the worker turned ``WorkflowCancelled`` into a FAILED run.

Real app + Postgres + Redis + an out-of-process workflow worker.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

from tests.e2e_full._wf_worker import (
    API,
    create_workflow,
    pending_approval_for,
    poll_run,
    workflow_worker,
)

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_GATE = {
    "name": "Cancel at gate",
    "steps": [
        {
            "id": "gate",
            "type": "hitl",
            "assignee": {"strategy": "specific", "specific_user": "anonymous"},
            "actions": [{"id": "approve"}, {"id": "reject"}],
        },
        {"id": "after", "type": "transform", "input": {"x": 1}, "depends_on": ["gate"]},
    ],
}


@pytest.fixture(scope="module")
def celery_worker(app: Any, tmp_path_factory: Any) -> Iterator[dict[str, Any]]:
    with workflow_worker(tmp_path_factory.mktemp("cancelworker"), name="wfcancele2e") as w:
        yield w


async def _parked_run(client: Any) -> tuple[str, str]:
    wid = await create_workflow(client, _GATE, name=f"cancel-gate-{uuid.uuid4().hex[:8]}")
    trig = await client.post(
        f"{API}/workflows/{wid}/trigger", json={"inputs": {}, "dry_run": False}
    )
    assert trig.status_code == 202, trig.text
    run_id = str(trig.json()["run_id"])
    await poll_run(client, run_id, {"waiting_hitl"})
    approval = await pending_approval_for(client, run_id)
    return run_id, str(approval["request_id"])


@pytest.mark.parametrize("late_action", ["approve", "reject"])
async def test_cancel_withdraws_approval_and_late_decision_is_409(
    tenant_client: Any, celery_worker: dict[str, Any], late_action: str
) -> None:
    run_id, request_id = await _parked_run(tenant_client)

    cancel = await tenant_client.post(f"{API}/runs/{run_id}/cancel")
    assert cancel.status_code == 202, cancel.text
    await poll_run(tenant_client, run_id, {"cancelled"})

    # The approval was withdrawn atomically with the cancel.
    got = await tenant_client.get(f"{API}/approvals/{request_id}")
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "cancelled", got.json()
    inbox = (await tenant_client.get(f"{API}/approvals")).json()["items"]
    assert request_id not in {i["request_id"] for i in inbox}

    late = await tenant_client.post(
        f"{API}/approvals/{request_id}/decide", json={"action": late_action}
    )
    assert late.status_code == 409, late.text
    assert "cancel" in late.text.lower()

    # The run stays cancelled (no resume was dispatched to flip it).
    await asyncio.sleep(3)
    run = (await tenant_client.get(f"{API}/runs/{run_id}")).json()
    assert run["status"] == "cancelled", run
    steps = (await tenant_client.get(f"{API}/runs/{run_id}/steps")).json()
    assert "after" not in {s["step_id"] for s in steps}, "a step ran after the cancel"


async def test_cancel_racing_a_decision_never_leaves_an_inconsistent_run(
    tenant_client: Any, celery_worker: dict[str, Any]
) -> None:
    run_id, request_id = await _parked_run(tenant_client)

    cancel, decide = await asyncio.gather(
        tenant_client.post(f"{API}/runs/{run_id}/cancel"),
        tenant_client.post(f"{API}/approvals/{request_id}/decide", json={"action": "approve"}),
    )
    assert cancel.status_code in (202, 404), cancel.text
    assert decide.status_code in (200, 409), decide.text
    if cancel.status_code == 202:
        # Cancel won (or ran after the approve): the run ends CANCELLED, never
        # FAILED and never resurrected by the resume.
        final = await poll_run(tenant_client, run_id, {"cancelled", "failed", "complete"})
        await asyncio.sleep(3)
        final = (await tenant_client.get(f"{API}/runs/{run_id}")).json()
        assert final["status"] == "cancelled", final
    if decide.status_code == 409:
        got = (await tenant_client.get(f"{API}/approvals/{request_id}")).json()
        assert got["status"] == "cancelled", got
