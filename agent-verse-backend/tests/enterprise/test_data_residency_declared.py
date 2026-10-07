"""a10-F253-01 / a10-F253-02: data residency reports the deployment's real region.

``get_data_residency`` claimed primary us-east-1 / backup eu-west-1 for every
tenant of every deployment, the frontend's ``region`` field was missing, and
``/compliance/regions`` appended a fabricated catalog compared on that missing
key (us-east-1 listed twice). Both now report the operator-declared
``DATA_REGION`` / ``DATA_BACKUP_REGION`` (nothing when undeclared); the GDPR EU
residency control falls back to the same source instead of assuming us-east-1.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.enterprise.compliance import ComplianceController
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="t-res", plan=PlanTier.ENTERPRISE, api_key_id="k")
_KEY = "av_enterprise_residency"


@pytest.fixture
def regions(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    def _set(primary: str = "", backup: str = "") -> None:
        monkeypatch.setenv("DATA_REGION", primary)
        monkeypatch.setenv("DATA_BACKUP_REGION", backup)
        get_settings.cache_clear()

    yield _set
    get_settings.cache_clear()


def test_undeclared_region_is_reported_as_unconfigured(regions: Any) -> None:
    regions()
    res = ComplianceController().get_data_residency(tenant_ctx=_CTX)
    assert res["region"] == "unconfigured"
    assert res["primary_region"] is None
    assert res["backup_region"] is None
    assert res["residency_configured"] is False
    assert res["per_tenant_residency"] is False
    assert "us-east-1" not in str(res) and "eu-west-1" not in str(res)


def test_declared_regions_are_reported(regions: Any) -> None:
    regions("eu-central-1", "eu-west-1")
    res = ComplianceController().get_data_residency(tenant_ctx=_CTX)
    assert res["region"] == res["primary_region"] == "eu-central-1"
    assert res["backup_region"] == "eu-west-1"
    assert res["residency_configured"] is True
    assert "eu-central-1" in res["description"]


def _client() -> TestClient:
    from app.api.enterprise import router

    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    app.state.compliance_controller = ComplianceController()
    return TestClient(app)


def test_regions_list_only_the_deployments_regions_without_duplicates(regions: Any) -> None:
    regions("ap-south-1", "ap-southeast-1")
    body = _client().get("/enterprise/compliance/regions", headers={"X-API-Key": _KEY}).json()
    assert [r["region"] for r in body] == ["ap-south-1", "ap-southeast-1"]
    assert [r["role"] for r in body] == ["primary", "backup"]


def test_regions_empty_when_undeclared(regions: Any) -> None:
    regions()
    body = _client().get("/enterprise/compliance/regions", headers={"X-API-Key": _KEY}).json()
    assert body == []


def test_backup_equal_to_primary_is_not_listed_twice(regions: Any) -> None:
    regions("eu-west-1", "eu-west-1")
    body = _client().get("/enterprise/compliance/regions", headers={"X-API-Key": _KEY}).json()
    assert [r["region"] for r in body] == ["eu-west-1"]


async def test_gdpr_residency_control_uses_the_declared_region_not_us_east_1(
    regions: Any,
) -> None:
    from app.enterprise.compliance_v2 import ComplianceChecker

    class _NoRow:
        def fetchone(self) -> None:
            return None

    class _Db:
        async def execute(self, *_: Any, **__: Any) -> _NoRow:
            return _NoRow()

    checker = ComplianceChecker(None)
    regions("eu-north-1")
    assert await checker._get_data_region(_Db(), "t") == "eu-north-1"
    regions()
    assert await checker._get_data_region(_Db(), "t") == "unconfigured"
