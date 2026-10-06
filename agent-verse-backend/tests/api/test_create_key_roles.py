"""QA-3: POST /tenants/me/keys accepts an optional ``roles`` list.

Keys created from the UI used to always get the default ``operator`` role with
no way to choose. A caller may now pick roles from ``VALID_ROLES``, but only
roles it holds itself (with hierarchy: an admin may mint any role, an operator
may mint operator/viewer). Roles are persisted, returned and used for auth.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.services.tenant_service import TenantService
from app.tenancy.middleware import TenantMiddleware


async def _setup() -> tuple[TestClient, TenantService, dict[str, str], str]:
    svc = TenantService()
    t = await svc.create_tenant(name="Roles Corp", email="roles@example.com")
    # Lift the FREE plan's 2-key cap so the tests can mint several keys.
    await svc.update_plan(t["tenant_id"], "enterprise")
    app = FastAPI()
    app.state.tenant_service = svc
    app.add_middleware(TenantMiddleware, key_resolver=svc.resolve_api_key)
    app.include_router(tenants_router)
    client = TestClient(app, raise_server_exceptions=False)
    return client, svc, {"X-API-Key": t["api_key"]}, t["tenant_id"]


async def test_default_role_is_operator_and_is_returned() -> None:
    client, svc, admin_h, _ = await _setup()
    resp = client.post("/tenants/me/keys", json={"name": "ui"}, headers=admin_h)
    assert resp.status_code == 201, resp.text
    assert resp.json()["roles"] == ["operator"]
    ctx = await svc.resolve_api_key(resp.json()["raw_key"])
    assert ctx is not None and ctx.roles == ("operator",)


async def test_admin_can_mint_a_viewer_key_and_it_resolves_as_viewer() -> None:
    client, svc, admin_h, tid = await _setup()
    resp = client.post(
        "/tenants/me/keys", json={"name": "ro", "roles": ["viewer"]}, headers=admin_h
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["roles"] == ["viewer"]
    ctx = await svc.resolve_api_key(resp.json()["raw_key"])
    assert ctx is not None and ctx.roles == ("viewer",)
    listed = {k["key_id"]: k for k in await svc.list_api_keys(tid)}
    assert listed[resp.json()["key_id"]]["roles"] == ["viewer"]


async def test_admin_can_mint_an_admin_key() -> None:
    client, _, admin_h, _ = await _setup()
    resp = client.post(
        "/tenants/me/keys", json={"name": "adm", "roles": ["admin"]}, headers=admin_h
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["roles"] == ["admin"]


async def test_unknown_role_is_422() -> None:
    client, _, admin_h, _ = await _setup()
    resp = client.post(
        "/tenants/me/keys", json={"name": "x", "roles": ["superuser"]}, headers=admin_h
    )
    assert resp.status_code == 422, resp.text


async def test_operator_cannot_mint_a_role_it_does_not_hold() -> None:
    client, _, admin_h, _ = await _setup()
    op = client.post(
        "/tenants/me/keys", json={"name": "op", "roles": ["operator"]}, headers=admin_h
    ).json()
    op_h = {"X-API-Key": op["raw_key"]}
    for role in ("admin", "approver"):
        denied = client.post(
            "/tenants/me/keys", json={"name": "esc", "roles": [role]}, headers=op_h
        )
        assert denied.status_code == 403, denied.text
    # ...but may mint roles it holds (operator implies viewer).
    ok = client.post("/tenants/me/keys", json={"name": "ro", "roles": ["viewer"]}, headers=op_h)
    assert ok.status_code == 201, ok.text


async def test_viewer_default_role_never_exceeds_the_caller() -> None:
    """A caller without operator must not get an operator key by default."""
    client, _, admin_h, _ = await _setup()
    viewer = client.post(
        "/tenants/me/keys", json={"name": "v", "roles": ["viewer"]}, headers=admin_h
    ).json()
    resp = client.post(
        "/tenants/me/keys", json={"name": "child"}, headers={"X-API-Key": viewer["raw_key"]}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["roles"] == ["viewer"]


async def test_rotation_keeps_the_rotated_keys_roles() -> None:
    client, svc, admin_h, tid = await _setup()
    owner_key_id = next(k["key_id"] for k in await svc.list_api_keys(tid))
    resp = client.post(
        f"/tenants/me/keys/{owner_key_id}/rotate", json={"revoke_old": False}, headers=admin_h
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["new_key"]["roles"] == ["admin"]


async def test_operator_cannot_rotate_an_admin_key_into_a_new_admin_key() -> None:
    client, svc, admin_h, tid = await _setup()
    owner_key_id = next(k["key_id"] for k in await svc.list_api_keys(tid))
    op = client.post(
        "/tenants/me/keys", json={"name": "op", "roles": ["operator"]}, headers=admin_h
    ).json()
    resp = client.post(
        f"/tenants/me/keys/{owner_key_id}/rotate",
        json={"revoke_old": False},
        headers={"X-API-Key": op["raw_key"]},
    )
    assert resp.status_code == 403, resp.text
