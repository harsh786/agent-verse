"""e2e_full: org RBAC must be fail-closed end-to-end.

A key that was never granted an admin/org role must be DENIED admin-gated org
endpoints — not silently elevated. This drives the real path: a freshly minted
API key (default ``operator`` role → resolves to ``viewer``) is authenticated by
the real TenantMiddleware and hits ``require_org_role`` on live endpoints.

Positive controls prove the same endpoints work for the owner (admin) key, so a
403 for the viewer is a genuine authorization denial, not a broken route.
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _mint_viewer_client(app: Any, owner_client: Any) -> AsyncClient:
    """Create a second API key on the same tenant and return a client using it.

    ``POST /tenants/me/keys`` mints a key with the default ``operator`` role — it
    is authenticated, but carries no admin/org role, so org RBAC resolves it to
    ``viewer`` (fail-closed). That is exactly the actor the negative path needs.
    """
    resp = await owner_client.post("/tenants/me/keys", json={"name": "viewer-key", "scopes": []})
    assert resp.status_code == 201, f"key create failed: {resp.status_code} {resp.text}"
    raw_key = resp.json()["raw_key"]
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://e2e-full",
        headers={"X-API-Key": raw_key},
    )


async def test_viewer_key_is_denied_admin_org_endpoints(app: Any, tenant_client: Any) -> None:
    # Owner (admin) creates an org — positive control that the route works.
    name = f"RBAC Org {uuid.uuid4().hex[:8]}"
    created = await tenant_client.post("/v1/org", json={"name": name})
    assert created.status_code in (200, 201), f"create: {created.status_code} {created.text}"
    org_id = created.json()["id"]

    viewer = await _mint_viewer_client(app, tenant_client)
    try:
        # 1) The viewer key IS authenticated and CAN read (viewer ⊇ read).
        got = await viewer.get(f"/v1/org/{org_id}")
        assert got.status_code == 200, f"viewer read should pass: {got.status_code} {got.text}"
        assert got.json()["id"] == org_id

        # 2) update() requires dept_admin → viewer must be denied (403, not 401/500).
        upd = await viewer.patch(f"/v1/org/{org_id}", json={"description": "hijacked"})
        assert upd.status_code == 403, f"viewer update must be forbidden: {upd.status_code} {upd.text}"

        # 3) delete() requires org_admin → viewer must be denied.
        deleted = await viewer.delete(f"/v1/org/{org_id}")
        assert deleted.status_code == 403, (
            f"viewer delete must be forbidden: {deleted.status_code} {deleted.text}"
        )

        # 4) The denial did not mutate anything — the org is still there, unchanged.
        still = await tenant_client.get(f"/v1/org/{org_id}")
        assert still.status_code == 200
        assert still.json()["name"] == name  # description was NOT changed to "hijacked"
    finally:
        await viewer.aclose()

    # 5) Positive control: the OWNER (admin) key CAN perform the same admin action.
    owner_del = await tenant_client.delete(f"/v1/org/{org_id}")
    assert owner_del.status_code in (200, 204), f"owner delete: {owner_del.status_code} {owner_del.text}"
