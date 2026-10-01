"""e2e_full (WF-30): published definitions are immutable; runs finish on their version.

Real Postgres + a real Celery worker: publish v1, start a run that suspends at an
approval, try to edit the live workflow (409), unpublish + edit + republish v2,
approve -> the suspended run finishes on the v1 graph while a new run uses v2.
With requires_publish_approval the edited draft cannot go live by itself.
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


def _definition(marker: str) -> dict[str, Any]:
    return {
        "name": "pinned",
        "steps": [
            gate(),
            {"id": f"after_{marker}", "type": "transform", "input": {"which": marker},
             "depends_on": ["gate"]},
        ],
    }


async def test_suspended_run_finishes_on_its_published_version(
    tenant_client: Any,
    celery_worker: dict[str, Any],  # noqa: F811
) -> None:
    wid = await create_workflow(tenant_client, _definition("v1"))
    pub = await tenant_client.post(f"{API}/workflows/{wid}/publish")
    assert pub.status_code == 200, pub.text

    run_v1 = await trigger(tenant_client, wid)
    await poll_run(tenant_client, run_v1, {"waiting_hitl"})
    request_id = await pending_approval(tenant_client, run_v1)

    # The live definition cannot be edited in place (PATCH and legacy PUT).
    patch = await tenant_client.patch(
        f"{API}/workflows/{wid}", json={"definition": _definition("v2")}
    )
    assert patch.status_code == 409, patch.text
    legacy = await tenant_client.put(
        f"/workflows/{wid}", json={"name": "x", "definition": _definition("v2")}
    )
    assert legacy.status_code == 409, legacy.text

    assert (await tenant_client.post(f"{API}/workflows/{wid}/unpublish")).status_code == 200
    patch = await tenant_client.patch(
        f"{API}/workflows/{wid}", json={"definition": _definition("v2")}
    )
    assert patch.status_code == 200, patch.text
    assert (await tenant_client.post(f"{API}/workflows/{wid}/publish")).status_code == 200

    decide = await tenant_client.post(
        f"{API}/approvals/{request_id}/decide", json={"action": "approve"}
    )
    assert decide.status_code == 200, decide.text
    done = await poll_run(tenant_client, run_v1, {"complete", "failed", "paused"})
    assert done["status"] == "complete", done
    steps = await steps_by_id(tenant_client, run_v1)
    assert "after_v1" in steps and "after_v2" not in steps, sorted(steps)

    # A run started after the republish executes v2.
    run_v2 = await trigger(tenant_client, wid)
    await poll_run(tenant_client, run_v2, {"waiting_hitl"})
    rid2 = await pending_approval(tenant_client, run_v2)
    await tenant_client.post(f"{API}/approvals/{rid2}/decide", json={"action": "approve"})
    await poll_run(tenant_client, run_v2, {"complete"})
    assert "after_v2" in await steps_by_id(tenant_client, run_v2)


async def test_edit_cannot_go_live_without_publish_approval(
    tenant_client: Any,
    celery_worker: dict[str, Any],  # noqa: F811
) -> None:
    wid = await create_workflow(tenant_client, _definition("v1"))
    assert (await tenant_client.post(f"{API}/workflows/{wid}/publish")).status_code == 200
    gated = await tenant_client.patch(
        f"{API}/workflows/{wid}", json={"requires_publish_approval": True}
    )
    assert gated.status_code == 200, gated.text

    edit = await tenant_client.patch(
        f"{API}/workflows/{wid}", json={"definition": _definition("v2")}
    )
    assert edit.status_code == 409, edit.text

    assert (await tenant_client.post(f"{API}/workflows/{wid}/unpublish")).status_code == 200
    edit = await tenant_client.patch(
        f"{API}/workflows/{wid}", json={"definition": _definition("v2")}
    )
    assert edit.status_code == 200, edit.text
    direct = await tenant_client.post(f"{API}/workflows/{wid}/publish")
    assert direct.status_code == 409, direct.text
    detail = await tenant_client.get(f"{API}/workflows/{wid}")
    assert detail.json()["status"] != "published"
