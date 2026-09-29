"""Regression: identity-critical operations were open to any authenticated key.

* POST /enterprise/saml/configure and /enterprise/scim/provision-token decide who
  can log in / be provisioned as the tenant;
* DELETE /tenants/me schedules erasure of the whole tenant;
* SCIM fell back to permissive create/update defaults on a config read error.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import app.api.enterprise as ent
from app.api.tenants import router as tenants_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware


def _ctx(*roles: str) -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.ENTERPRISE, api_key_id="k", roles=roles)


def _request(ctx: TenantContext) -> Any:
    req = MagicMock()
    req.state = SimpleNamespace(tenant=ctx)
    req.app.state = SimpleNamespace(db_session_factory=MagicMock())
    return req


@pytest.mark.parametrize("roles", [(), ("operator",), ("viewer",)])
async def test_non_admin_cannot_configure_saml(roles: tuple[str, ...]) -> None:
    body = ent.SAMLConfigRequest(
        idp_entity_id="https://idp", idp_sso_url="https://idp/sso", idp_cert="C",
        sp_entity_id="sp",
    )
    with pytest.raises(HTTPException) as exc:
        await ent.configure_saml(_request(_ctx(*roles)), body)
    assert exc.value.status_code == 403


async def test_non_admin_cannot_provision_scim_token() -> None:
    with pytest.raises(HTTPException) as exc:
        await ent.provision_scim_token(_request(_ctx("operator")))
    assert exc.value.status_code == 403


async def test_scim_config_read_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _auth(_r: Any) -> str:
        return "t1"

    monkeypatch.setattr("app.auth.scim_handler.require_scim_auth", _auth)

    def _broken() -> Any:
        raise RuntimeError("db down")

    req = MagicMock()
    req.app.state = SimpleNamespace(db_session_factory=_broken)
    with pytest.raises(HTTPException) as exc:
        await ent._get_scim_handler(req)
    assert exc.value.status_code == 503


@pytest.mark.parametrize("roles,expected", [(("operator",), 403), (("admin",), 200)])
def test_tenant_deletion_is_admin_only(roles: tuple[str, ...], expected: int) -> None:
    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=AsyncMock(return_value=_ctx(*roles)))
    app.include_router(tenants_router)
    controller = MagicMock()
    controller.request_data_deletion = AsyncMock(return_value={"status": "pending"})
    app.state.compliance_controller = controller
    r = TestClient(app, raise_server_exceptions=False).delete(
        "/tenants/me", headers={"X-API-Key": "k"}
    )
    assert r.status_code == expected
    assert controller.request_data_deletion.await_count == (1 if expected == 200 else 0)
