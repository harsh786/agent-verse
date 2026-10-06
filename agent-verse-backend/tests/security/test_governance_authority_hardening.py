"""Regressions: governance authority that any key (or a forged field) could claim."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware


def _ctx(roles: tuple[str, ...], key: str = "k1") -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.FREE, api_key_id=key, roles=roles)


def _client(router: Any, ctx: TenantContext, **state: Any) -> TestClient:
    async def _resolve(key: str) -> TenantContext | None:
        return ctx if key == "key" else None

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    for k, v in state.items():
        setattr(app.state, k, v)
    return TestClient(app, raise_server_exceptions=False)


def test_only_admins_can_mint_or_revoke_grants_and_grantor_is_the_caller() -> None:
    from app.api.grants import router
    from app.governance.grants import InMemoryGrantStore

    store = InMemoryGrantStore()
    body = {"grantee_agent_id": "a1", "scopes": ["*"], "grantor": "user:ceo", "ttl_seconds": 3600}
    viewer = _client(router, _ctx(("viewer",)), grant_store=store)
    assert viewer.post("/grants", json=body, headers={"X-API-Key": "key"}).status_code == 403
    assert viewer.post("/grants/g1/revoke", headers={"X-API-Key": "key"}).status_code == 403

    from app.api.agents import AgentStore

    agents = AgentStore()
    agents._data[(_ctx(("admin",)).tenant_id, "a1")] = {"agent_id": "a1"}
    admin = _client(
        router, _ctx(("admin",), key="admin-key"), grant_store=store, agent_store=agents
    )
    r = admin.post("/grants", json=body, headers={"X-API-Key": "key"})
    assert r.status_code == 201, r.text
    assert r.json()["grantor"] == "key:admin-key"


def test_unregistered_write_routes_are_read_only_for_viewers() -> None:
    from app.auth.scope_enforcement import _may_write_unregistered

    assert _may_write_unregistered(("viewer",), "/skills") is False
    assert _may_write_unregistered(("approver",), "/triggers/x") is False
    assert _may_write_unregistered(("approver",), "/governance/approvals/a/approve") is True
    assert _may_write_unregistered(("operator",), "/skills") is True
    assert _may_write_unregistered(("admin",), "/billing/x") is True


def test_hitl_approver_is_the_authenticated_key_not_the_body() -> None:
    from app.api.governance import _approver_identity

    assert _approver_identity(_ctx(("approver",), key="kid-7")) == "kid-7"
    with pytest.raises(Exception):
        _approver_identity(_ctx(("approver",), key=""))


async def test_compliance_ceiling_fails_closed_on_lookup_error() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.services.goal_service import compliance_autonomy_ceiling

    state = SimpleNamespace(compliance_bundle_store=object())
    with patch(
        "app.governance.compliance_bundles.effective_max_autonomy_for",
        side_effect=RuntimeError("db down"),
    ):
        assert await compliance_autonomy_ceiling(state, tenant_id="t1") == "supervised"


def test_stream_token_secret_refuses_the_public_default_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.auth import stream_tokens

    for var in ("STREAM_TOKEN_SECRET", "GOAL_TOKEN_SECRET", "VAULT_MASTER_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError):
        stream_tokens._signing_secret()
    monkeypatch.setenv("VAULT_MASTER_KEY", "m" * 32)
    assert stream_tokens._signing_secret() != stream_tokens._DEV_SECRET
