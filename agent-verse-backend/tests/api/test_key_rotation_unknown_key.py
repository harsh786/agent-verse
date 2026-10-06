"""QA-20: rotating a key id that does not exist (for THIS tenant) must not mint a key.

Regression: ``rotate_key`` created the replacement key before checking that the
old one existed, then swallowed the revoke's NotFoundError and answered
201 ``old_revoked: false`` — leaking a brand-new key for any made-up id (and for
another tenant's key id).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.services.tenant_service import TenantService
from app.tenancy.middleware import TenantMiddleware


def _client() -> tuple[TestClient, TenantService, dict[str, str], dict[str, str]]:
    svc = TenantService()
    a = asyncio.run(svc.create_tenant("Tenant A", "rot-a@example.com"))
    b = asyncio.run(svc.create_tenant("Tenant B", "rot-b@example.com"))
    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=svc.resolve_api_key)
    app.include_router(tenants_router)
    app.state.tenant_service = svc
    return TestClient(app, raise_server_exceptions=False), svc, a, b


def _active_keys(svc: TenantService, tenant_id: str) -> int:
    keys = asyncio.run(svc.list_api_keys(tenant_id))
    return sum(1 for k in keys if k["is_active"])


@pytest.mark.parametrize("revoke_old", [True, False])
def test_rotate_unknown_key_is_404_and_mints_nothing(revoke_old: bool) -> None:
    client, svc, a, _ = _client()
    before = _active_keys(svc, a["tenant_id"])
    resp = client.post(
        "/tenants/me/keys/does-not-exist/rotate",
        json={"revoke_old": revoke_old},
        headers={"X-API-Key": a["api_key"]},
    )
    assert resp.status_code == 404, resp.text
    assert "new_key" not in resp.json()
    assert _active_keys(svc, a["tenant_id"]) == before


def test_rotate_other_tenants_key_is_404_and_leaves_it_active() -> None:
    client, svc, a, b = _client()
    resp = client.post(
        f"/tenants/me/keys/{b['api_key_id']}/rotate",
        json={"revoke_old": True},
        headers={"X-API-Key": a["api_key"]},
    )
    assert resp.status_code == 404, resp.text
    assert _active_keys(svc, a["tenant_id"]) == 1
    assert _active_keys(svc, b["tenant_id"]) == 1


def test_rotate_own_key_still_works() -> None:
    client, svc, a, _ = _client()
    resp = client.post(
        f"/tenants/me/keys/{a['api_key_id']}/rotate",
        json={"revoke_old": True},
        headers={"X-API-Key": a["api_key"]},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["old_revoked"] is True
    assert body["new_key"]["raw_key"]
