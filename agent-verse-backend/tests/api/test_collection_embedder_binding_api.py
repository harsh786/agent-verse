"""Per-collection embedders through the knowledge API.

``POST /knowledge/collections`` binds a collection to a configured embedding
model (``embedding_model``) and its width; ``GET /knowledge/collections``
reports each collection's embedder, model key, width and chunk table;
``GET /knowledge/embedders`` lists what a new collection can be bound to. A
model whose width has no chunk table, or that is not configured, is a clear 422.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.ai_router.registry_store import ModelRegistryStore
from app.api.knowledge import router as knowledge_router
from app.core.config import Settings
from app.rag.semantic_cache import SemanticCache
from app.rag.store import KnowledgeStore
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_ISOLATE_PROVIDER_ENV = True

_CTX = TenantContext(tenant_id="tid-binding", plan=PlanTier.ENTERPRISE, api_key_id="kid-b")
_KEY = "av_test_collection_embedder_binding"
_AUTH = {"X-API-Key": _KEY}
_NEMOTRON = "nvidia/nemotron-3-embed-1b"
_QWEN = "Qwen/Qwen3-Embedding-0.6B"
_GEMINI = "gemini-embedding-001"


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    def get(self, k: str) -> Any:
        return self.store.get(k)

    def set(self, k: str, v: Any) -> None:
        self.store[k] = v

    def incr(self, k: str) -> int:
        self.store[k] = str(int(self.store.get(k) or 0) + 1)
        return int(self.store[k])


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> Any:
    import app.ai_router.registry_store as rs
    import app.ai_router.selection as sel

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(rs, "_store", ModelRegistryStore(_FakeRedis()))
    model_registry.clear_configured()
    model_registry.set_preferences({})
    for provider, model_id, base_url, extra in (
        ("onprem", _QWEN, "http://192.168.63.104:30082/v1", {}),
        ("gemini", _GEMINI, None, {"dimensions": 3072}),
        ("onprem", "acme/embed-wide", "http://192.168.63.105:8000/v1", {"dimensions": 4096}),
    ):
        model_registry.register_configured(
            ModelEndpoint(
                provider=provider,
                model_id=model_id,
                display_name=model_id,
                capabilities=[ModelCapability.EMBEDDING],
                base_url=base_url,
                extra={"source": "override", **extra},
            )
        )
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


class _Nemotron:
    """The deployment default embedder: NVIDIA nemotron, 2048-d."""

    _embed_model = _NEMOTRON
    embedding_dim = 2048


class _Resolution:
    provider = "nvidia"
    model = _NEMOTRON
    dimension = 2048


def _client() -> TestClient:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(knowledge_router)
    app.state.knowledge_store = KnowledgeStore(embedding_dim=2048)
    app.state.semantic_cache = SemanticCache()
    app.state.embedder = _Nemotron()
    app.state.embedder_resolution = _Resolution()
    app.state.settings = Settings(_env_file=None, embedding_dim=2048)  # type: ignore[call-arg]
    return TestClient(app, raise_server_exceptions=False)


def test_gemini_3072_and_qwen_1024_collections_sit_next_to_the_nvidia_2048_default() -> None:
    client = _client()
    default = client.post("/knowledge/collections", json={"name": "nvidia"}, headers=_AUTH)
    gemini = client.post(
        "/knowledge/collections",
        json={"name": "gemini", "embedding_model": f"gemini/{_GEMINI}"},
        headers=_AUTH,
    )
    qwen = client.post(
        "/knowledge/collections", json={"name": "qwen", "embedding_model": _QWEN}, headers=_AUTH
    )
    for resp in (default, gemini, qwen):
        assert resp.status_code == 201, resp.text
    assert default.json()["embedding_model_key"] == f"nvidia/{_NEMOTRON}"
    assert (gemini.json()["embedding_dim"], gemini.json()["chunk_table"]) == (
        3072, "knowledge_chunks_3072")
    assert qwen.json()["embedding_provider"] == "onprem"

    listed = {c["name"]: c for c in client.get("/knowledge/collections", headers=_AUTH).json()}
    assert {n: (c["embedding_model"], c["embedding_dim"], c["embedding_binding"])
            for n, c in listed.items()} == {
        "nvidia": (_NEMOTRON, 2048, "explicit"),
        "gemini": (_GEMINI, 3072, "explicit"),
        "qwen": (_QWEN, 1024, "explicit"),
    }
    assert listed["qwen"]["embedder"] == _QWEN
    assert listed["qwen"]["chunk_table"] == "knowledge_chunks_1024"


def test_the_embedders_listing_offers_each_model_with_its_width() -> None:
    body = _client().get("/knowledge/embedders", headers=_AUTH).json()
    by_key = {e["key"]: e for e in body["embedders"]}
    assert by_key["default"]["model"] == _NEMOTRON and by_key["default"]["dimension"] == 2048
    assert by_key[f"onprem/{_QWEN}"]["dimension"] == 1024
    assert by_key[f"onprem/{_QWEN}"]["available"] is True
    assert by_key[f"gemini/{_GEMINI}"]["chunk_table"] == "knowledge_chunks_3072"
    wide = by_key["onprem/acme/embed-wide"]
    assert wide["available"] is False and "no chunk table" in wide["reason"]
    assert body["default_dimension"] == 2048
    assert body["supported_dimensions"] == [768, 1024, 1536, 2048, 3072]


def test_a_width_without_a_chunk_table_is_refused_clearly() -> None:
    client = _client()
    resp = client.post(
        "/knowledge/collections",
        json={"name": "wide", "embedding_model": "acme/embed-wide"},
        headers=_AUTH,
    )
    assert resp.status_code == 422
    assert "4096-d" in resp.json()["detail"] and "supported: 768, 1024" in resp.json()["detail"]
    assert client.get("/knowledge/collections", headers=_AUTH).json() == []


def test_an_unconfigured_model_is_refused_naming_what_is_configured() -> None:
    resp = _client().post(
        "/knowledge/collections",
        json={"name": "oa", "embedding_model": "text-embedding-3-large"},
        headers=_AUTH,
    )
    assert resp.status_code == 422
    assert "not configured" in resp.json()["detail"] and _QWEN in resp.json()["detail"]


def test_the_legacy_embedder_type_field_binds_a_configured_model_too() -> None:
    resp = _client().post(
        "/knowledge/collections", json={"name": "q", "embedder_type": _QWEN}, headers=_AUTH
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["embedding_dim"] == 1024
