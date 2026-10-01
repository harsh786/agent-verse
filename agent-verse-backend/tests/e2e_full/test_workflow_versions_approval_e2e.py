"""e2e_full (WF-ROUTES-500): workflow version detail / diff and publish approval.

Against the real app + Postgres + Redis:

* every publish records a version in ``workflow_definition_versions`` —
  list, detail and diff are served from it (the detail/diff routes used to 500
  because the service methods did not exist, and nothing wrote versions);
* ``requires_publish_approval`` refuses a direct publish (409); a submission
  goes ``pending_approval``; the submitter cannot approve it (four-eyes); a
  second API key of the same tenant approves and the workflow goes live with the
  approver recorded;
* another tenant sees none of it (RLS).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]

_API = "/api/v1"


def _definition(*step_ids: str) -> dict[str, Any]:
    return {
        "name": "E2E Versions WF",
        "steps": [{"id": sid, "type": "transform", "input": {"v": sid}} for sid in step_ids],
    }


async def _create(client: Any, *step_ids: str) -> str:
    resp = await client.post(
        f"{_API}/workflows",
        json={"name": f"e2e-ver-{uuid.uuid4().hex[:8]}", "definition": _definition(*step_ids)},
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _second_key_client(app: Any, tenant_client: Any) -> tuple[Any, str]:
    """A client on a second API key of the SAME tenant, and that key's id."""
    from httpx import ASGITransport, AsyncClient

    resp = await tenant_client.post("/tenants/me/keys", json={"name": "approver"})
    assert resp.status_code == 201, resp.text
    client = AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": resp.json()["raw_key"]},
    )
    return client, str(resp.json()["key_id"])


async def test_publish_records_versions_detail_and_diff(tenant_client: Any) -> None:
    wid = await _create(tenant_client, "a", "b")

    pub1 = await tenant_client.post(f"{_API}/workflows/{wid}/publish")
    assert pub1.status_code == 200, pub1.text
    v1 = int(pub1.json()["version"])

    await tenant_client.post(f"{_API}/workflows/{wid}/unpublish")
    patched = await tenant_client.patch(
        f"{_API}/workflows/{wid}", json={"definition": _definition("a", "c")}
    )
    assert patched.status_code == 200, patched.text
    pub2 = await tenant_client.post(f"{_API}/workflows/{wid}/publish")
    assert pub2.status_code == 200, pub2.text
    v2 = int(pub2.json()["version"])

    listing = await tenant_client.get(f"{_API}/workflows/{wid}/versions")
    assert listing.status_code == 200, listing.text
    assert {v["version"] for v in listing.json()} == {str(v1), str(v2)}

    detail = await tenant_client.get(f"{_API}/workflows/{wid}/versions/{v1}")
    assert detail.status_code == 200, detail.text
    assert [s["id"] for s in detail.json()["definition"]["steps"]] == ["a", "b"]

    missing = await tenant_client.get(f"{_API}/workflows/{wid}/versions/9999")
    assert missing.status_code == 404

    diff = await tenant_client.get(f"{_API}/workflows/{wid}/versions/{v1}/diff/{v2}")
    assert diff.status_code == 200, diff.text
    body = diff.json()
    assert body["added_steps"] == ["c"]
    assert body["removed_steps"] == ["b"]

    # Restore brings the v1 definition back as the current draft definition.
    await tenant_client.post(f"{_API}/workflows/{wid}/unpublish")
    restored = await tenant_client.post(f"{_API}/workflows/{wid}/versions/{v1}/restore")
    assert restored.status_code == 200, restored.text
    current = await tenant_client.get(f"{_API}/workflows/{wid}")
    assert [s["id"] for s in current.json()["definition"]["steps"]] == ["a", "b"]


async def test_publish_approval_flow_is_four_eyes(app: Any, tenant_client: Any) -> None:
    wid = await _create(tenant_client, "a")
    on = await tenant_client.patch(
        f"{_API}/workflows/{wid}", json={"requires_publish_approval": True}
    )
    assert on.status_code == 200, on.text
    got = await tenant_client.get(f"{_API}/workflows/{wid}")
    assert got.json()["requires_publish_approval"] is True

    direct = await tenant_client.post(f"{_API}/workflows/{wid}/publish")
    assert direct.status_code == 409, direct.text

    submitted = await tenant_client.post(f"{_API}/workflows/{wid}/submit-for-approval")
    assert submitted.status_code == 202, submitted.text
    assert submitted.json()["status"] == "pending_approval"

    # Edits are frozen while pending.
    edit = await tenant_client.patch(
        f"{_API}/workflows/{wid}", json={"definition": _definition("sneaky")}
    )
    assert edit.status_code == 409, edit.text

    # The submitter cannot approve their own request — even naming someone else.
    self_approve = await tenant_client.post(
        f"{_API}/workflows/{wid}/approve-publish",
        json={"note": "me", "approver_id": str(uuid.uuid4())},
    )
    assert self_approve.status_code == 409, self_approve.text

    approver, approver_key_id = await _second_key_client(app, tenant_client)
    async with approver:
        approved = await approver.post(
            f"{_API}/workflows/{wid}/approve-publish", json={"note": "LGTM"}
        )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "published"
    assert body["publish_approved_by"] == approver_key_id
    assert body["submitted_by"] and body["submitted_by"] != approver_key_id

    versions = await tenant_client.get(f"{_API}/workflows/{wid}/versions")
    assert versions.status_code == 200
    assert len(versions.json()) == 1
    assert uuid.UUID(versions.json()[0]["published_by"]) == uuid.UUID(approver_key_id)


async def test_reject_returns_to_draft(app: Any, tenant_client: Any) -> None:
    wid = await _create(tenant_client, "a")
    await tenant_client.patch(f"{_API}/workflows/{wid}", json={"requires_publish_approval": True})
    submitted = await tenant_client.post(f"{_API}/workflows/{wid}/submit-for-approval")
    assert submitted.status_code == 202, submitted.text
    rejected = await tenant_client.post(
        f"{_API}/workflows/{wid}/reject-publish", json={"note": "no"}
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "draft"


async def test_versions_are_tenant_scoped(app: Any, client: Any, tenant_client: Any) -> None:
    from httpx import ASGITransport, AsyncClient

    wid = await _create(tenant_client, "a")
    pub = await tenant_client.post(f"{_API}/workflows/{wid}/publish")
    assert pub.status_code == 200, pub.text
    version = int(pub.json()["version"])

    signup = await client.post(
        "/tenants/signup",
        json={"name": "Other", "email": f"other-{uuid.uuid4().hex[:10]}@example.com"},
    )
    assert signup.status_code == 201, signup.text
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": signup.json()["api_key"]},
    ) as other:
        listing = await other.get(f"{_API}/workflows/{wid}/versions")
        assert listing.status_code == 200
        assert listing.json() == []
        detail = await other.get(f"{_API}/workflows/{wid}/versions/{version}")
        assert detail.status_code == 404
