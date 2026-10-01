"""e2e_full (LEGACY-WF-ROUTER): legacy /workflows routes enforce the workflow ACL.

The visual builder saves/runs through the legacy ``PUT /workflows/{id}`` and
``POST /workflows/{id}/run`` (plus ``DELETE``), which skipped the per-workflow
ACL and the pending-approval freeze that ``/api/v1/workflows`` enforces.
Against the real app + Postgres, with two API keys of one tenant: the owner
(tenant admin) and a second ``operator`` key given per-workflow grants.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_API = "/api/v1"
_BODY = {"name": "renamed", "description": "", "definition": {"steps": []}}


async def test_legacy_routes_follow_the_workflow_acl(app: Any, tenant_client: Any) -> None:
    from httpx import ASGITransport, AsyncClient

    created = await tenant_client.post(
        "/workflows",
        json={
            "name": f"legacy-{uuid.uuid4().hex[:8]}",
            "definition": {"steps": [{"id": "s1", "type": "transform", "input": {"a": 1}}]},
        },
    )
    assert created.status_code == 201, created.text
    wid = created.json()["id"]

    key = await tenant_client.post("/tenants/me/keys", json={"name": "colleague"})
    assert key.status_code == 201, key.text
    colleague_id = key.json()["key_id"]

    async def _grant(level: str) -> str:
        resp = await tenant_client.post(
            f"{_API}/workflows/{wid}/permissions",
            json={"subject": colleague_id, "role": level, "subject_type": "user"},
        )
        assert resp.status_code == 201, resp.text
        return str(resp.json()["id"])

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": key.json()["raw_key"]},
    ) as colleague:
        viewer_grant = await _grant("viewer")
        assert (await colleague.get(f"/workflows/{wid}")).status_code == 200
        assert (await colleague.put(f"/workflows/{wid}", json=_BODY)).status_code == 403
        assert (await colleague.post(f"/workflows/{wid}/run?dry_run=true")).status_code == 403
        assert (await colleague.delete(f"/workflows/{wid}")).status_code == 403

        # Upgrade the colleague to runner: may run, still may not edit.
        removed = await tenant_client.delete(f"{_API}/workflows/{wid}/permissions/{viewer_grant}")
        assert removed.status_code == 204, removed.text
        await _grant("runner")
        run = await colleague.post(f"/workflows/{wid}/run?dry_run=true")
        assert run.status_code == 202, run.text
        assert (await colleague.put(f"/workflows/{wid}", json=_BODY)).status_code == 403

    # The owner (tenant admin) still edits through the legacy route...
    assert (await tenant_client.put(f"/workflows/{wid}", json=_BODY)).status_code == 204

    # ...but not while the workflow is pending publish approval.
    on = await tenant_client.patch(
        f"{_API}/workflows/{wid}", json={"requires_publish_approval": True}
    )
    assert on.status_code == 200, on.text
    sub = await tenant_client.post(f"{_API}/workflows/{wid}/submit-for-approval")
    assert sub.status_code == 202, sub.text
    frozen = await tenant_client.put(f"/workflows/{wid}", json=_BODY)
    assert frozen.status_code == 409, frozen.text
