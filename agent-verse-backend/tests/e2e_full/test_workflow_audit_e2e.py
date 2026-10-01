"""e2e_full (WF-AUDIT): workflow lifecycle actions land in the audit trail.

The workflow audit middleware called ``audit_log.record`` with the wrong
arguments (every emit raised and was swallowed) and the API never audited
workflow actions at all. Against the real app + Postgres this proves create /
update / publish / run / publish-approval each write an ``audit_log`` row,
readable through ``GET /governance/audit`` by the owning tenant only.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_API = "/api/v1"
_EXPECTED = {
    "workflow.created",
    "workflow.updated",
    "workflow.published",
    "workflow.run_triggered",
    "workflow.unpublished",
    "workflow.publish_submitted",
    "workflow.publish_approved",
}


async def _audit_rows(client: Any, workflow_id: str) -> list[dict[str, Any]]:
    resp = await client.get("/governance/audit", params={"goal_id": workflow_id, "limit": 200})
    assert resp.status_code == 200, resp.text
    rows: list[dict[str, Any]] = resp.json()
    return rows


async def test_workflow_actions_are_audited_per_tenant(
    app: Any, client: Any, tenant_client: Any
) -> None:
    from httpx import ASGITransport, AsyncClient

    created = await tenant_client.post(
        f"{_API}/workflows",
        json={
            "name": f"audit-{uuid.uuid4().hex[:8]}",
            "definition": {
                "name": "Audit WF",
                "steps": [{"id": "s1", "type": "transform", "input": {"a": 1}}],
            },
        },
    )
    assert created.status_code == 201, created.text
    wid = created.json()["id"]

    upd = await tenant_client.patch(f"{_API}/workflows/{wid}", json={"description": "v2"})
    assert upd.status_code == 200, upd.text
    pub = await tenant_client.post(f"{_API}/workflows/{wid}/publish")
    assert pub.status_code == 200, pub.text
    trig = await tenant_client.post(
        f"{_API}/workflows/{wid}/trigger", json={"inputs": {}, "dry_run": True}
    )
    assert trig.status_code == 202, trig.text
    assert (await tenant_client.post(f"{_API}/workflows/{wid}/unpublish")).status_code == 200

    on = await tenant_client.patch(
        f"{_API}/workflows/{wid}", json={"requires_publish_approval": True}
    )
    assert on.status_code == 200, on.text
    sub = await tenant_client.post(f"{_API}/workflows/{wid}/submit-for-approval")
    assert sub.status_code == 202, sub.text
    key = await tenant_client.post("/tenants/me/keys", json={"name": "approver"})
    assert key.status_code == 201, key.text
    approver_key_id = key.json()["key_id"]
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": key.json()["raw_key"]},
    ) as approver:
        ok = await approver.post(f"{_API}/workflows/{wid}/approve-publish", json={"note": "ok"})
    assert ok.status_code == 200, ok.text

    # Audit persistence is fire-and-forget: poll the DB-backed query briefly.
    rows: list[dict[str, Any]] = []
    for _ in range(40):
        rows = await _audit_rows(tenant_client, wid)
        if _EXPECTED <= {r["tool_name"] for r in rows}:
            break
        await asyncio.sleep(0.25)
    tools = {r["tool_name"] for r in rows}
    assert _EXPECTED <= tools, sorted(tools)
    approval = next(r for r in rows if r["tool_name"] == "workflow.publish_approved")
    assert approval["approver"] == approver_key_id

    # Tenant-scoped: another tenant's audit query for the same id sees nothing.
    signup = await client.post(
        "/tenants/signup",
        json={"name": "Other", "email": f"audit-other-{uuid.uuid4().hex[:10]}@example.com"},
    )
    assert signup.status_code == 201, signup.text
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": signup.json()["api_key"]},
    ) as other:
        assert await _audit_rows(other, wid) == []
