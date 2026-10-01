"""SVC-31: only tenant admins may change the tenant's BYOK provider key / base_url.

PUT /tenants/me/llm and PUT /tenants/me/llm-config had no role check and no
ENDPOINT_SCOPES entry, so any operator key could repoint all of the tenant's
LLM traffic (prompts with goal data and tool outputs) to a host it controls.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.auth.scope_enforcement import ScopeEnforcementMiddleware
from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_KEYS = {
    "k-admin": ("admin",),
    "k-operator": ("operator",),
    "k-viewer": ("viewer",),
}


def _app() -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        roles = _KEYS.get(key)
        if roles is None:
            return None
        return TenantContext(tenant_id="t-llm", plan=PlanTier.STARTER, api_key_id=key, roles=roles)

    app.add_middleware(ScopeEnforcementMiddleware)
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(tenants_router)
    app.state.audit_log = AuditLog()
    return app


_BODY = {"provider": "openai", "api_key": "sk-test-abcdefghijklmnop", "default_model": "gpt-4o"}


@pytest.mark.parametrize("key", ["k-operator", "k-viewer"])
def test_non_admin_cannot_set_llm_key(key: str) -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    resp = client.put("/tenants/me/llm", json=_BODY, headers={"X-API-Key": key})
    assert resp.status_code == 403
    assert (
        client.get("/tenants/me/llm", headers={"X-API-Key": "k-admin"}).json()["configured"]
        is False
    )


@pytest.mark.parametrize("key", ["k-operator", "k-viewer"])
def test_non_admin_cannot_repoint_llm_config(key: str) -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    assert (
        client.put("/tenants/me/llm", json=_BODY, headers={"X-API-Key": "k-admin"}).status_code
        == 200
    )
    resp = client.put(
        "/tenants/me/llm-config",
        json={"base_url": "https://attacker.example.com/v1"},
        headers={"X-API-Key": key},
    )
    assert resp.status_code == 403
    cfg = client.get("/tenants/me/llm", headers={"X-API-Key": "k-admin"}).json()
    assert not cfg.get("base_url")


def test_admin_can_set_and_change_is_audited() -> None:
    app = _app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.put("/tenants/me/llm", json=_BODY, headers={"X-API-Key": "k-admin"})
    assert resp.status_code == 200
    resp = client.put(
        "/tenants/me/llm-config",
        json={"default_model": "gpt-4o-mini"},
        headers={"X-API-Key": "k-admin"},
    )
    assert resp.status_code == 200
    events: list[Any] = app.state.audit_log._log.get("t-llm", [])
    assert [e.tool_name for e in events] == ["tenant.llm_config", "tenant.llm_config"]
    assert all(e.api_key_id == "k-admin" for e in events)
    assert "provider=openai" in events[0].note


def test_llm_config_base_url_is_ssrf_checked() -> None:
    """The non-secret PUT used to skip the base_url allow-list the key PUT applies."""
    client = TestClient(_app(), raise_server_exceptions=False)
    client.put("/tenants/me/llm", json=_BODY, headers={"X-API-Key": "k-admin"})
    resp = client.put(
        "/tenants/me/llm-config",
        json={"base_url": "http://169.254.169.254/latest"},
        headers={"X-API-Key": "k-admin"},
    )
    assert resp.status_code == 422


def test_get_reports_whether_caller_can_edit() -> None:
    client = TestClient(_app(), raise_server_exceptions=False)
    assert client.get("/tenants/me/llm", headers={"X-API-Key": "k-admin"}).json()["can_edit"]
    assert not client.get("/tenants/me/llm", headers={"X-API-Key": "k-operator"}).json()["can_edit"]
    assert not client.get("/tenants/me/llm-config", headers={"X-API-Key": "k-viewer"}).json()[
        "can_edit"
    ]


def test_scope_registry_covers_llm_writes() -> None:
    from app.auth.scope_enforcement import ScopeEnforcementMiddleware as M

    assert M._required_scope("PUT", "/tenants/me/llm") == "tenancy:write"
    assert M._required_scope("PUT", "/tenants/me/llm-config") == "tenancy:write"
