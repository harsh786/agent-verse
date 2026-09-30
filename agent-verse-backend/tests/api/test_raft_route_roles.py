"""KB-31: paid RAFT fine-tune actions require the admin role.

Only the generic unregistered-write fallback guarded RAFT job creation,
deploy and evaluate, which start paid provider fine-tunes and inference.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.rag_platform import router as rag_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTXS = {
    "k-admin": TenantContext("t-raft", PlanTier.ENTERPRISE, "ka", roles=("admin",)),
    "k-operator": TenantContext("t-raft", PlanTier.ENTERPRISE, "ko", roles=("operator",)),
    "k-viewer": TenantContext("t-raft", PlanTier.ENTERPRISE, "kv", roles=("viewer",)),
}

_WRITES = [
    ("/rag/raft/jobs", {"dataset_id": "d", "provider_id": "p", "base_model": "m"}),
    ("/rag/raft/jobs/j1/refresh", None),
    ("/rag/raft/jobs/j1/reconcile", None),
    ("/rag/raft/jobs/j1/evaluate", None),
    ("/rag/raft/jobs/j1/deploy", None),
]


class _Service:
    def __getattr__(self, name: str) -> Any:
        async def _call(*_a: Any, **_k: Any) -> Any:
            raise LookupError("reached the service")

        return _call


def _client() -> TestClient:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return _CTXS.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(rag_router)
    app.state.raft_service = _Service()
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("key", ["k-operator", "k-viewer"])
@pytest.mark.parametrize(("path", "body"), _WRITES)
def test_non_admin_keys_are_forbidden(key: str, path: str, body: Any) -> None:
    resp = _client().post(path, json=body, headers={"X-API-Key": key})
    assert resp.status_code == 403, resp.text


@pytest.mark.parametrize(("path", "body"), _WRITES)
def test_admin_keys_get_past_the_role_check(path: str, body: Any) -> None:
    resp = _client().post(path, json=body, headers={"X-API-Key": "k-admin"})
    assert resp.status_code != 403, resp.text
