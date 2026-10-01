"""e2e_full (WF-34): variables set before an approval survive the resume.

Real Postgres + a real Celery worker: ``set_variable x -> gate -> transform
{{vars.x}}``. The resume is reconstructed from the database in a worker; the
variable comes back from the step's persisted state delta, not the definition
default.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.e2e_full._wf_e2e import (
    API,
    create_workflow,
    gate,
    pending_approval,
    poll_run,
    steps_by_id,
    trigger,
)
from tests.e2e_full.test_workflow_run_e2e import celery_worker  # noqa: F401

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def test_variable_set_before_approval_is_used_after_resume(
    tenant_client: Any,
    celery_worker: dict[str, Any],  # noqa: F811
) -> None:
    wid = await create_workflow(
        tenant_client,
        {
            "name": "vars across approval",
            "vars": {"x": "default"},
            "steps": [
                {"id": "setx", "type": "set_variable", "var_name": "x", "var_value": "from-step"},
                gate(depends_on=["setx"]),
                {"id": "use", "type": "transform", "input": {"v": "{{vars.x}}"},
                 "depends_on": ["gate"]},
            ],
        },
    )
    run_id = await trigger(tenant_client, wid)
    await poll_run(tenant_client, run_id, {"waiting_hitl"})
    request_id = await pending_approval(tenant_client, run_id)
    decide = await tenant_client.post(
        f"{API}/approvals/{request_id}/decide", json={"action": "approve"}
    )
    assert decide.status_code == 200, decide.text
    done = await poll_run(tenant_client, run_id, {"complete", "failed", "paused"})
    assert done["status"] == "complete", done
    steps = await steps_by_id(tenant_client, run_id)
    assert steps["use"]["output"] == {"v": "from-step"}, steps["use"]
    assert steps["setx"]["status"] == "complete"
