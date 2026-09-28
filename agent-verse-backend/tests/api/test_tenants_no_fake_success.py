"""Regression tests: /tenants/me endpoints never report success for no-ops.

1. ``DELETE /tenants/me`` answered ``scheduled_for_deletion`` and did nothing.
2. ``PUT /tenants/me/notifications`` answered ``updated`` with no Redis and on a
   Redis error (and stored prefs expired after 30 days); the body was unvalidated.
3. ``POST /tenants/me/roles`` returned a fabricated id with no database.
4. ``POST /tenants/me/ip-allowlist`` returned 201 with no database — for a CIDR
   stored, and therefore enforced, nowhere.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import fakeredis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-nf", plan=PlanTier.FREE, api_key_id="k", roles=("admin",))
H = {"X-API-Key": "key-nf"}


def _app(**state: Any) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == "key-nf" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(tenants_router)
    for k, v in state.items():
        setattr(app.state, k, v)
    return TestClient(app, raise_server_exceptions=False)


def test_delete_me_records_a_durable_erasure_job() -> None:
    controller = MagicMock()
    controller.request_data_deletion = AsyncMock(
        return_value={"status": "pending", "scheduled_at": "2026-10-28T00:00:00+00:00"}
    )
    resp = _app(compliance_controller=controller).delete("/tenants/me", headers=H)
    assert resp.status_code == 200
    assert resp.json()["status"] == "scheduled_for_deletion"
    assert resp.json()["scheduled_at"].startswith("2026-10-28")
    controller.request_data_deletion.assert_awaited_once()


def test_delete_me_is_503_when_the_job_cannot_be_recorded() -> None:
    controller = MagicMock()
    controller.request_data_deletion = AsyncMock(side_effect=RuntimeError("db down"))
    resp = _app(compliance_controller=controller).delete("/tenants/me", headers=H)
    assert resp.status_code == 503


def test_notifications_put_requires_a_store() -> None:
    body = {"goalComplete": False}
    assert (
        _app(_redis=None).put("/tenants/me/notifications", json=body, headers=H).status_code == 503
    )
    broken = MagicMock()
    broken.set = AsyncMock(side_effect=ConnectionError("down"))
    assert (
        _app(_redis=broken).put("/tenants/me/notifications", json=body, headers=H).status_code
        == 503
    )


def test_notifications_round_trip_without_expiry_and_validated() -> None:
    redis = fakeredis.FakeAsyncRedis()
    client = _app(_redis=redis)
    assert (
        client.put("/tenants/me/notifications", json={"goalComplete": False}, headers=H).status_code
        == 200
    )
    got = client.get("/tenants/me/notifications", headers=H).json()
    assert got["goalComplete"] is False
    assert got["goalFailed"] is True  # defaults kept for unspecified keys
    bad = client.put("/tenants/me/notifications", json={"evil": "x"}, headers=H)
    assert bad.status_code == 422

    import asyncio

    assert asyncio.run(redis.ttl("notif_prefs:tid-nf")) == -1  # never expires


def test_roles_and_ip_allowlist_writes_need_a_database() -> None:
    client = _app(db_session_factory=None)
    role = client.post("/tenants/me/roles", json={"user_id": "u1", "role": "viewer"}, headers=H)
    assert role.status_code == 503
    cidr = client.post("/tenants/me/ip-allowlist", json={"cidr": "203.0.113.0/24"}, headers=H)
    assert cidr.status_code == 503
