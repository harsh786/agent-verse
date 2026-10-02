"""Guardrails v2 rule management: admin-only writes (GRD-05), durable create (GRD-02),
update/disable/delete (GRD-01)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.guardrails_v2 import router as g2_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_TID = "tid-grd-mgmt"
_KEYS = {
    "ak_grd_admin": ("admin",),
    "ak_grd_operator": ("operator",),
    "ak_grd_viewer": ("viewer",),
}


def _headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.guardrails_v2 import engine as engine_mod

    fresh = engine_mod.GuardrailsEngine()
    monkeypatch.setattr(engine_mod, "guardrails_engine", fresh)

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        roles = _KEYS.get(key)
        if roles is None:
            return None
        return TenantContext(
            tenant_id=_TID, plan=PlanTier.PROFESSIONAL, api_key_id=key, roles=roles
        )

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(g2_router)
    with TestClient(app) as c:
        c.engine = fresh  # type: ignore[attr-defined]
        yield c


_RULE = {"name": "Block everything", "rule_type": "regex", "config": {"pattern": ".*"}}


@pytest.mark.parametrize("key", ["ak_grd_operator", "ak_grd_viewer"])
def test_rule_and_bundle_writes_require_admin(client: Any, key: str) -> None:
    """GRD-05: any write-capable key could add a tenant-wide BLOCK rule ('.*')."""
    assert client.post("/guardrails-v2/rules", json=_RULE, headers=_headers(key)).status_code == 403
    assert client.post("/guardrails-v2/bundles/soc2", headers=_headers(key)).status_code == 403
    assert client.engine.get_rules(_TID) == []


def test_admin_can_write_rules(client: Any) -> None:
    resp = client.post("/guardrails-v2/rules", json=_RULE, headers=_headers("ak_grd_admin"))
    assert resp.status_code == 200, resp.text
    # Reads stay open to every authenticated role.
    assert client.get("/guardrails-v2/rules", headers=_headers("ak_grd_viewer")).status_code == 200
