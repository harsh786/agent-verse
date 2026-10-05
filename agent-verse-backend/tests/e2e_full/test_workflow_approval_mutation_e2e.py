"""e2e_full (WF-41): once decided, an approval cannot be reopened by delegate/escalate.

Real Postgres + a real Celery worker: the run suspends at its gate (approval
written by the worker), the API delegates it (pending: allowed), decides it,
and a late delegate / escalate answers 409 while the run resumes exactly once
to complete.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.e2e_full._wf_e2e import API, create_workflow, gate, pending_approval, poll_run, trigger
from tests.e2e_full.test_workflow_run_e2e import celery_worker  # noqa: F401

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def test_late_delegate_and_escalate_cannot_reopen_a_decision(
    tenant_client: Any,
    celery_worker: dict[str, Any],  # noqa: F811
) -> None:
    wid = await create_workflow(
        tenant_client,
        {"name": "gate", "steps": [gate(), {"id": "after", "type": "transform",
                                            "input": {"ok": 1}, "depends_on": ["gate"]}]},
    )
    run_id = await trigger(tenant_client, wid)
    await poll_run(tenant_client, run_id, {"waiting_hitl"})
    rid = await pending_approval(tenant_client, run_id)

    comment = await tenant_client.post(
        f"{API}/approvals/{rid}/delegate", json={"to_user_id": "anonymous", "note": "mine"}
    )
    assert comment.status_code == 200, comment.text

    decide = await tenant_client.post(f"{API}/approvals/{rid}/decide", json={"action": "approve"})
    assert decide.status_code == 200, decide.text

    late = await tenant_client.post(
        f"{API}/approvals/{rid}/delegate", json={"to_user_id": "someone-else"}
    )
    assert late.status_code == 409, late.text
    late_esc = await tenant_client.post(f"{API}/approvals/{rid}/escalate")
    assert late_esc.status_code == 409, late_esc.text

    detail = await tenant_client.get(f"{API}/approvals/{rid}")
    assert detail.status_code == 200, detail.text
    # An approve decision leaves the approval "approved" (WF-APPROVAL-WORKFLOW-ID,
    # 703bcac38: approve/reject no longer fall through to the generic "decided").
    assert detail.json()["status"] == "approved", detail.json()
    assert detail.json()["discussion"][0]["type"] == "delegation"

    done = await poll_run(tenant_client, run_id, {"complete", "failed", "paused"})
    assert done["status"] == "complete", done
