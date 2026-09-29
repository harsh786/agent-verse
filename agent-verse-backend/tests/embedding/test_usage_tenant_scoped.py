"""Regression: GET /embeddings/usage returned process-global counters.

``embedding_router`` is a process-wide singleton and its usage/error counters
were keyed by model only, so every tenant's ``/embeddings/usage`` showed every
other tenant's embedding volume. Counters are now kept per tenant and the
endpoint returns only the caller's.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.embeddings import router as embeddings_router
from app.embedding.router import EmbeddingRouter
from app.providers.fake import FakeProvider
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_A = TenantContext(tenant_id="tid-emb-a", plan=PlanTier.PROFESSIONAL, api_key_id="ka")
_B = TenantContext(tenant_id="tid-emb-b", plan=PlanTier.PROFESSIONAL, api_key_id="kb")
_KEYS = {"ak_emb_a": _A, "ak_emb_b": _B}


async def test_router_usage_is_kept_per_tenant() -> None:
    router = EmbeddingRouter(provider=FakeProvider(embed_dim=8))
    await router.embed_texts_report(["one two three"], tenant_id="a")
    await router.embed_texts_report(["four"], tenant_id="b")
    a = router.get_usage_stats(tenant_id="a")
    b = router.get_usage_stats(tenant_id="b")
    assert sum(a["usage_by_model"].values()) == 3
    assert sum(b["usage_by_model"].values()) == 1
    assert router.get_usage_stats(tenant_id="c") == {"usage_by_model": {}, "errors_by_model": {}}


def test_usage_endpoint_shows_only_the_callers_usage() -> None:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _KEYS.get(key)

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(embeddings_router)
    app.state.embedder = FakeProvider(embed_dim=8)
    client = TestClient(app)
    resp = client.post(
        "/embeddings/embed",
        json={"texts": ["tenant a private text"]},
        headers={"X-API-Key": "ak_emb_a"},
    )
    assert resp.status_code == 200, resp.text
    a = client.get("/embeddings/usage", headers={"X-API-Key": "ak_emb_a"}).json()
    b = client.get("/embeddings/usage", headers={"X-API-Key": "ak_emb_b"}).json()
    assert sum(a["usage_by_model"].values()) >= 4
    assert b == {"usage_by_model": {}, "errors_by_model": {}}
