"""KB-25: POST/GET /knowledge/collections/{id}/re-embed.

``re_embed_collection`` had no API or beat caller, so a deployment that changed
embedders could not re-embed its existing collections at all.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.rag import reembed
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware
from tests.rag.reembed_fakes import FakeRedis

_CTXS = {
    "k-admin": TenantContext("t-re", PlanTier.ENTERPRISE, "ka", roles=("admin",)),
    "k-operator": TenantContext("t-re", PlanTier.ENTERPRISE, "ko", roles=("operator",)),
    "k-other-admin": TenantContext("t-other", PlanTier.ENTERPRISE, "kx", roles=("admin",)),
}


class _Store:
    async def get_collection_async(self, collection_id: str, *, tenant_ctx: Any) -> Any:
        if collection_id == "c1" and tenant_ctx.tenant_id == "t-re":
            return object()
        return None

    async def collection_counters_async(self, *, tenant_ctx: Any, collection_id: str) -> Any:
        return [{"collection_id": collection_id, "chunk_count": 120}]


def _client(redis: Any) -> TestClient:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return _CTXS.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(knowledge_router)
    app.state.knowledge_store = _Store()
    app.state._redis = redis
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def enqueue() -> Any:
    with patch("app.scaling.tasks.re_embed_collection.apply_async") as apply_async:
        yield apply_async


def test_admin_enqueues_a_re_embed_with_progress(enqueue: MagicMock) -> None:
    redis = FakeRedis()
    resp = _client(redis).post(
        "/knowledge/collections/c1/re-embed", headers={"X-API-Key": "k-admin"}
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    assert resp.json()["status"] == "queued"
    enqueue.assert_called_once()
    kwargs = enqueue.call_args.kwargs
    assert kwargs["queue"] == "maintenance"
    assert kwargs["kwargs"] == {"tenant_id": "t-re", "collection_id": "c1", "job_id": job_id}
    assert redis.data[reembed.lock_key("t-re", "c1")] == job_id
    progress = json.loads(redis.data[reembed.progress_key("t-re", "c1")])
    assert progress["status"] == "queued" and progress["job_id"] == job_id
    # KB-49: the 202 says what the run will reserve (3 batches of 50 chunks).
    assert resp.json()["chunk_count"] == 120
    assert resp.json()["estimated_cost_usd"] == pytest.approx(0.0003)

    status = _client(redis).get(
        "/knowledge/collections/c1/re-embed", headers={"X-API-Key": "k-operator"}
    )
    assert status.status_code == 200
    assert status.json()["job_id"] == job_id


def test_a_second_re_embed_while_one_runs_is_409(enqueue: MagicMock) -> None:
    redis = FakeRedis()
    client = _client(redis)
    first = client.post("/knowledge/collections/c1/re-embed", headers={"X-API-Key": "k-admin"})
    assert first.status_code == 202
    second = client.post("/knowledge/collections/c1/re-embed", headers={"X-API-Key": "k-admin"})
    assert second.status_code == 409
    assert enqueue.call_count == 1


def test_non_admin_is_forbidden(enqueue: MagicMock) -> None:
    resp = _client(FakeRedis()).post(
        "/knowledge/collections/c1/re-embed", headers={"X-API-Key": "k-operator"}
    )
    assert resp.status_code == 403
    enqueue.assert_not_called()


def test_another_tenants_collection_is_404(enqueue: MagicMock) -> None:
    client = _client(FakeRedis())
    post = client.post("/knowledge/collections/c1/re-embed", headers={"X-API-Key": "k-other-admin"})
    get = client.get("/knowledge/collections/c1/re-embed", headers={"X-API-Key": "k-other-admin"})
    assert post.status_code == 404 and get.status_code == 404
    enqueue.assert_not_called()


def test_without_redis_it_fails_closed(enqueue: MagicMock) -> None:
    client = _client(None)
    assert (
        client.post("/knowledge/collections/c1/re-embed", headers={"X-API-Key": "k-admin"})
    ).status_code == 503
    assert (
        client.get("/knowledge/collections/c1/re-embed", headers={"X-API-Key": "k-admin"})
    ).status_code == 503
    enqueue.assert_not_called()


def test_enqueue_failure_releases_the_lock_and_is_503(enqueue: MagicMock) -> None:
    enqueue.side_effect = RuntimeError("broker down")
    redis = FakeRedis()
    resp = _client(redis).post(
        "/knowledge/collections/c1/re-embed", headers={"X-API-Key": "k-admin"}
    )
    assert resp.status_code == 503
    assert reembed.lock_key("t-re", "c1") not in redis.data
    assert json.loads(redis.data[reembed.progress_key("t-re", "c1")])["status"] == "failed"


def test_status_before_any_run_is_never_run() -> None:
    resp = _client(FakeRedis()).get(
        "/knowledge/collections/c1/re-embed", headers={"X-API-Key": "k-admin"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "never_run"
