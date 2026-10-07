"""USR-3: a collection reports the embedder / model / dimension actually used.

Every collection used to report ``"voyage"``: the label was the client's
``embedder_type`` (default ``"voyage"``) and the stored row fell back to
``"voyage"`` too, while the vectors came from whatever embedder the deployment
runs (here a local sentence-transformers model). The collection API now reports
the deployment's real embedder and its output dimension. A request naming an
embedder that is not configured is refused rather than stored as a label that
does not match the vectors (configured registry models CAN be bound per
collection: tests/api/test_collection_embedder_binding_api.py).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.knowledge import router as knowledge_router
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="tid-embid", plan=PlanTier.PROFESSIONAL, api_key_id="kid-embid")
_KEY = "av_test_collection_embedder_identity"
_AUTH = {"X-API-Key": _KEY}


class _LocalMpnet:
    """Stands in for LocalEmbedProvider (all-mpnet-base-v2, 768-d) — not voyage."""

    _model_name = "all-mpnet-base-v2"
    embedding_dim = 768


def _client(embedder: Any = None) -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(knowledge_router)
    app.state.knowledge_store = KnowledgeStore(embedding_dim=768)
    app.state.semantic_cache = SemanticCache()
    if embedder is not None:
        app.state.embedder = embedder
    return TestClient(app, raise_server_exceptions=False)


def test_new_collection_reports_the_real_non_voyage_embedder() -> None:
    client = _client(_LocalMpnet())
    created = client.post("/knowledge/collections", json={"name": "docs"}, headers=_AUTH)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["embedder"] == "all-mpnet-base-v2"
    assert body["embedding_dim"] == 768

    listed = client.get("/knowledge/collections", headers=_AUTH).json()
    assert [(c["embedder"], c["embedding_dim"]) for c in listed] == [("all-mpnet-base-v2", 768)]


def test_a_default_embedder_request_uses_the_deployment_embedder() -> None:
    client = _client(_LocalMpnet())
    for hint in ("default", "auto", "all-mpnet-base-v2", "ALL_MPNET_BASE_V2"):
        resp = client.post(
            "/knowledge/collections", json={"name": f"c-{hint}", "embedder_type": hint},
            headers=_AUTH,
        )
        assert resp.status_code == 201, (hint, resp.text)
        assert resp.json()["embedder"] == "all-mpnet-base-v2"


def test_naming_a_different_embedder_is_refused_not_mislabelled() -> None:
    client = _client(_LocalMpnet())
    resp = client.post(
        "/knowledge/collections", json={"name": "v", "embedder_type": "voyage"}, headers=_AUTH
    )
    assert resp.status_code == 422
    assert "all-mpnet-base-v2" in resp.json()["detail"]
    assert client.get("/knowledge/collections", headers=_AUTH).json() == []


def test_without_an_embedder_the_label_is_unknown_never_voyage() -> None:
    client = _client(None)
    resp = client.post("/knowledge/collections", json={"name": "n"}, headers=_AUTH)
    assert resp.status_code == 201, resp.text
    assert resp.json()["embedder"] == "unknown"
    refused = client.post(
        "/knowledge/collections", json={"name": "m", "embedder_type": "voyage"}, headers=_AUTH
    )
    assert refused.status_code == 422
