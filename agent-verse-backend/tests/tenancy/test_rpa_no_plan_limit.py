"""D5 — RPA has no plan limit (owner decision 2026-10-05).

Nothing may imply that browser automation is plan-gated: the ``rpa`` plan-feature
flag is gone from every tier, and a FREE-plan tenant can list and run RPA tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.rpa import router as rpa_router
from app.tenancy import entitlements
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_FREE = TenantContext(tenant_id="tid-free-rpa", plan=PlanTier.FREE, api_key_id="kid-free")
_KEY = "av_test_free_rpa"


def test_no_plan_tier_lists_an_rpa_feature_flag() -> None:
    for tier, features in entitlements._PLAN_FEATURES.items():
        assert "rpa" not in features, f"{tier.value} still carries an 'rpa' plan flag"


def test_entitlements_docstring_records_the_decision() -> None:
    doc = entitlements.__doc__ or ""
    assert "RPA" in doc and "no plan limit" in doc.lower()


@dataclass
class _Result:
    success: bool = True
    output: str = "navigated"
    artifact_url: str | None = None
    artifact_name: str | None = None
    duration_ms: float = 1.0
    error: str | None = None
    error_code: str | None = None
    error_detail: dict[str, Any] | None = None


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, **kwargs: Any) -> _Result:
        self.calls.append(kwargs)
        return _Result()


def _app(executor: _FakeExecutor) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _FREE if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(rpa_router)
    app.state.rpa_executor = executor
    return app


def test_free_plan_tenant_can_list_rpa_tools() -> None:
    client = TestClient(_app(_FakeExecutor()))
    resp = client.get("/rpa/tools", headers={"X-API-Key": _KEY})
    assert resp.status_code == 200
    assert resp.json()


def test_free_plan_tenant_can_run_an_rpa_tool() -> None:
    executor = _FakeExecutor()
    client = TestClient(_app(executor))
    tool = client.get("/rpa/tools", headers={"X-API-Key": _KEY}).json()[0]["name"]
    resp = client.post(
        "/rpa/execute",
        json={"tool_name": tool, "arguments": {}},
        headers={"X-API-Key": _KEY},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True
    assert executor.calls and executor.calls[0]["tenant_id"] == _FREE.tenant_id
