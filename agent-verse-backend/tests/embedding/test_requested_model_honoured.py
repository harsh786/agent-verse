"""KB-26: POST /embeddings/embed honours (or refuses) the requested model.

``embed_texts_report`` built ``EmbedRequest(texts=texts)`` without the model, so
a caller asking for model X silently got the default model's vectors.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.embeddings import router as embeddings_router
from app.providers.base import EmbedResponse
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="t-kb26", plan=PlanTier.PROFESSIONAL, api_key_id="k")
_KEY = "ak_kb26"


class _SingleModelEmbedder:
    """Serves one model; like a local embedder it ignores ``request.model``."""

    _embed_model = "nv-embedqa-e5-v5"

    def __init__(self) -> None:
        self.requested: list[str] = []

    async def embed(self, request: Any) -> EmbedResponse:
        self.requested.append(request.model)
        return EmbedResponse(
            embeddings=[[0.1, 0.2] for _ in request.texts], model="nvidia/nv-embedqa-e5-v5"
        )


def _client(embedder: Any) -> TestClient:
    app = FastAPI()

    async def resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=resolve)
    app.include_router(embeddings_router)
    app.state.embedder = embedder
    return TestClient(app)


def _embed(client: TestClient, **body: Any) -> Any:
    return client.post(
        "/embeddings/embed", json={"texts": ["hello"], **body}, headers={"X-API-Key": _KEY}
    )


def test_requested_model_is_forwarded_and_reported() -> None:
    embedder = _SingleModelEmbedder()
    resp = _embed(_client(embedder), provider="nvidia", model="nv-embedqa-e5-v5")
    assert resp.status_code == 200, resp.text
    assert embedder.requested == ["nv-embedqa-e5-v5"]
    assert resp.json()["model"] == "nvidia/nv-embedqa-e5-v5"


def test_a_model_the_provider_does_not_serve_is_422() -> None:
    embedder = _SingleModelEmbedder()
    resp = _embed(_client(embedder), provider="openai", model="text-embedding-3-large")
    assert resp.status_code == 422, resp.text
    assert "text-embedding-3-large" in resp.json()["detail"]


def test_without_a_model_the_configured_embedder_model_is_used() -> None:
    embedder = _SingleModelEmbedder()
    resp = _embed(_client(embedder))
    assert resp.status_code == 200, resp.text
    assert embedder.requested == [""]
    assert resp.json()["model"] == "nvidia/nv-embedqa-e5-v5"
