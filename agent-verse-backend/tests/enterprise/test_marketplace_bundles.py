"""Bundle deploys must report failures honestly — never a fabricated agent id.

v1 ``Marketplace.deploy`` caught any ``agent_store.create`` failure (and the
no-store case) and substituted ``uuid4().hex`` as the "deployed" agent id, so a
bundle whose agents were never created came back ``status: complete`` with ids
that pointed at nothing. ``POST /marketplace/bundles`` now deploys through
MarketplaceV2's atomic install and reports per-item status.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.enterprise import marketplace_router
from app.enterprise.marketplace import Marketplace
from app.enterprise.marketplace_v2 import MarketplaceV2
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

T = TenantContext(tenant_id="tenant-bundle", plan=PlanTier.ENTERPRISE, api_key_id="k")
_HDR = {"X-API-Key": "key"}


# ── v1 gallery ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_v1_deploy_raises_when_agent_store_fails() -> None:
    store = MagicMock()
    store.create = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError, match="db down"):
        await Marketplace(agent_store=store).deploy(
            template_id="tpl-bug-fix", params={}, tenant_ctx=T
        )


@pytest.mark.asyncio
async def test_v1_deploy_without_agent_store_raises() -> None:
    with pytest.raises(RuntimeError, match="agent store"):
        await Marketplace().deploy(template_id="tpl-bug-fix", params={}, tenant_ctx=T)


@pytest.mark.asyncio
async def test_v1_bundle_reports_failed_items_without_agent_ids() -> None:
    store = MagicMock()
    store.create = AsyncMock(side_effect=RuntimeError("db down"))
    result = await Marketplace(agent_store=store).create_bundle(
        name="B", template_ids=["tpl-bug-fix", "tpl-devops"], tenant_ctx=T
    )
    assert result["status"] == "failed"
    assert result["templates_deployed"] == 0
    assert result["results"] == []
    assert [i["status"] for i in result["items"]] == ["failed", "failed"]
    assert all("agent_id" not in i for i in result["items"])
    assert all("db down" in e["error"] for e in result["errors"])


@pytest.mark.asyncio
async def test_v1_bundle_partial_reports_real_agent_ids_only() -> None:
    store = MagicMock()
    store.create = AsyncMock(return_value="agent-real")
    result = await Marketplace(agent_store=store).create_bundle(
        name="B", template_ids=["tpl-bug-fix", "tpl-nope"], tenant_ctx=T
    )
    assert result["status"] == "partial"
    assert [i["status"] for i in result["items"]] == ["deployed", "failed"]
    assert result["items"][0]["agent_id"] == "agent-real"


# ── v2 service ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_v2_bundle_partial_with_per_item_status() -> None:
    svc = MarketplaceV2(db_factory=None)
    result = await svc.create_bundle(
        name="Ops",
        template_ids=["tpl-hr-onboarding", "tpl-missing"],
        tenant_ctx=T,
        params={"tpl-hr-onboarding": {"employee_name": "Ann"}},
    )
    assert result["status"] == "partial"
    assert result["templates_deployed"] == 1
    assert result["templates_failed"] == 1
    ok, bad = result["items"]
    assert ok["status"] == "deployed" and ok["template_id"] == "tpl-hr-onboarding"
    installs = await svc.list_installs(tenant_id=T.tenant_id)
    assert ok["agent_id"] == installs[0]["agent_id"]
    assert bad == {
        "template_id": "tpl-missing",
        "status": "failed",
        "error": "Template not found",
    }


@pytest.mark.asyncio
async def test_v2_bundle_all_failed_is_failed() -> None:
    svc = MarketplaceV2(db_factory=None)
    result = await svc.create_bundle(name="X", template_ids=["nope-1", "nope-2"], tenant_ctx=T)
    assert result["status"] == "failed"
    assert result["results"] == []
    assert all("agent_id" not in i for i in result["items"])


@pytest.mark.asyncio
async def test_v2_bundle_install_exception_is_a_failed_item() -> None:
    svc = MarketplaceV2(db_factory=None)
    svc.install = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
    # (per-item exceptions become failed items, they do not abort the bundle)
    result = await svc.create_bundle(name="X", template_ids=["tpl-devops"], tenant_ctx=T)
    assert result["status"] == "failed"
    assert result["items"] == [{"template_id": "tpl-devops", "status": "failed", "error": "boom"}]


@pytest.mark.asyncio
async def test_v2_bundle_empty_is_rejected() -> None:
    with pytest.raises(ValueError):
        await MarketplaceV2(db_factory=None).create_bundle(name="X", template_ids=[], tenant_ctx=T)


# ── endpoint ────────────────────────────────────────────────────────────────


def _client(v2: Any) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return T if key == "key" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(marketplace_router)
    app.state.marketplace_v2 = v2
    return TestClient(app, raise_server_exceptions=False)


def test_bundle_endpoint_complete_is_201() -> None:
    resp = _client(MarketplaceV2(db_factory=None)).post(
        "/marketplace/bundles",
        json={
            "name": "B",
            "template_ids": ["tpl-hr-onboarding"],
            "params": {"tpl-hr-onboarding": {"employee_name": "Ann"}},
        },
        headers=_HDR,
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["status"] == "complete"


def test_bundle_endpoint_partial_is_207() -> None:
    resp = _client(MarketplaceV2(db_factory=None)).post(
        "/marketplace/bundles",
        json={
            "name": "B",
            "template_ids": ["tpl-hr-onboarding", "tpl-missing"],
            "params": {"tpl-hr-onboarding": {"employee_name": "Ann"}},
        },
        headers=_HDR,
    )
    assert resp.status_code == 207, resp.text
    assert resp.json()["status"] == "partial"


def test_bundle_endpoint_all_failed_is_422_with_report() -> None:
    resp = _client(MarketplaceV2(db_factory=None)).post(
        "/marketplace/bundles",
        json={"name": "B", "template_ids": ["tpl-missing"]},
        headers=_HDR,
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["status"] == "failed"
    assert detail["items"][0]["status"] == "failed"


def test_bundle_endpoint_rejects_empty_bundle() -> None:
    resp = _client(MarketplaceV2(db_factory=None)).post(
        "/marketplace/bundles", json={"name": "B", "template_ids": []}, headers=_HDR
    )
    assert resp.status_code == 422
