"""/eval/golden-datasets must not report success for work it never does.

Regression: create / add-item / promote returned freshly minted ids and
``"status": "promoted"`` while persisting nothing, and promote accepted any goal
id — including another tenant's. No eval runner reads golden datasets (the
runnable golden tasks live in ``/ai-ops/datasets`` and ``/enterprise`` eval
suites), so the endpoints now answer an honest 501.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.golden_datasets import router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="gd-t1", plan=PlanTier.PROFESSIONAL, api_key_id="gd-key")
_HEADERS = {"X-API-Key": "gd-key"}


def _client() -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == "gd-key" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(router)
    return TestClient(app)


_CALLS = [
    ("get", "/eval/golden-datasets", None),
    ("post", "/eval/golden-datasets", {"name": "regressions"}),
    ("post", "/eval/golden-datasets/ds1/items", {"goal": "do x"}),
    ("post", "/eval/golden-datasets/promote-goal/someone-elses-goal", None),
]


@pytest.mark.parametrize(("method", "path", "body"), _CALLS)
def test_endpoints_are_an_honest_501(method: str, path: str, body: dict | None) -> None:
    resp = _client().request(method.upper(), path, json=body, headers=_HEADERS)
    assert resp.status_code == 501, resp.text
    detail = resp.json()["detail"]
    assert "/ai-ops/datasets" in detail
    assert "promoted" not in resp.text
    assert "id" not in resp.json()


@pytest.mark.parametrize(("method", "path", "body"), _CALLS)
def test_auth_is_still_required(method: str, path: str, body: dict | None) -> None:
    assert _client().request(method.upper(), path, json=body).status_code == 401
