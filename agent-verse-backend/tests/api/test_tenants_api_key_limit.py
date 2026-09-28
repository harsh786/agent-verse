"""Regression: the plan's ``max_api_keys`` is enforced on key creation.

``check_api_key_limit`` existed but nothing called it, so any plan (FREE:
2 keys) could mint unlimited API keys.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.services.tenant_service import TenantService
from app.tenancy.middleware import TenantMiddleware


async def _client() -> tuple[TestClient, dict[str, str]]:
    svc = TenantService()
    t = await svc.create_tenant(name="Acme", email="keys@acme.test")  # 1 key (FREE: max 2)
    app = FastAPI()
    app.state.tenant_service = svc
    app.add_middleware(TenantMiddleware, key_resolver=svc.resolve_api_key)
    app.include_router(tenants_router)
    return TestClient(app, raise_server_exceptions=False), {"X-API-Key": t["api_key"]}


async def test_free_plan_cannot_exceed_two_active_keys() -> None:
    client, h = await _client()
    assert client.post("/tenants/me/keys", json={"name": "k2"}, headers=h).status_code == 201
    over = client.post("/tenants/me/keys", json={"name": "k3"}, headers=h)
    assert over.status_code == 429
    assert over.json()["error"]["code"] == "PLAN_LIMIT_EXCEEDED"


async def test_rotation_at_the_limit_still_works_when_it_revokes_the_old_key() -> None:
    client, h = await _client()
    second = client.post("/tenants/me/keys", json={"name": "k2"}, headers=h).json()
    rotated = client.post(
        f"/tenants/me/keys/{second['key_id']}/rotate", json={"revoke_old": True}, headers=h
    )
    assert rotated.status_code == 201, rotated.text
    kept = client.post(
        f"/tenants/me/keys/{second['key_id']}/rotate", json={"revoke_old": False}, headers=h
    )
    assert kept.status_code == 429
