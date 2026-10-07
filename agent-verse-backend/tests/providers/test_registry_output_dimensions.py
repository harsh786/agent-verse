"""A configured embedding model's requested output width (``output_dimensions``).

Models that can shorten their vectors (Gemini ``gemini-embedding-001``: 128..3072,
OpenAI ``text-embedding-3-*``) are matched to the vector index width on purpose:

* ``POST /models/configured`` accepts ``output_dimensions`` (1..8192, null / 0 =
  native width, left out = keep), stores it and ``GET`` returns it;
* it is sent as ``dimensions`` on every OpenAI-compatible ``/embeddings``
  request: the registry embedder and the "Test connection" probe (Gemini's
  OpenAI-compatible endpoint takes ``{model, input, dimensions}`` with a Bearer
  key at ``/v1beta/openai/embeddings``);
* the dimension check accepts a model whose requested width equals the index
  (``EMBEDDING_DIM``) even when its native / catalog width differs.

HTTP is answered in-process by ``httpx.MockTransport`` (unit tests only).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.ai_router.registry_store import (
    ModelRegistryStore,
    get_model_registry_store,
    set_model_registry_store,
)
from app.api.model_registry import router as models_router
from app.core.config import Settings
from app.providers.base import EmbedRequest
from app.providers.embedder_factory import resolve_embedder
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_ISOLATE_PROVIDER_ENV = True

_EM = ModelCapability.EMBEDDING
_GEMINI_EMBED = "gemini-embedding-001"  # 3072-d natively (catalog)
_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai"
_ADMIN_CTX = TenantContext(
    tenant_id="tid-owner", plan=PlanTier.ENTERPRISE, api_key_id="kid-a", roles=("admin",)
)
_ADMIN = {"X-API-Key": "ak_admin"}
_EMBED_ENV = (
    "OPENAI_API_KEY",
    "VOYAGE_API_KEY",
    "GOOGLE_API_KEY",
    "NVIDIA_API_KEY",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL",
    "EMBEDDING_API_KEY",
    "EMBEDDING_DIM",
    "SENTENCE_TRANSFORMERS_MODEL",
    "OPENAI_BASE_URL",
    "ONPREM_API_KEY",
)


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
def _clean(monkeypatch: pytest.MonkeyPatch) -> Any:
    import app.ai_router.registry_store as rs
    import app.ai_router.selection as sel

    for name in _EMBED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(rs, "_store", ModelRegistryStore(_FakeRedis()))
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


class _Endpoint:
    """A Gemini-like OpenAI-compatible server: honours ``dimensions`` unless told not to."""

    def __init__(self, native: int, *, honour_dimensions: bool = True) -> None:
        self.native = native
        self.honour = honour_dimensions
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/models"):
            # Gemini's OpenAI-compatible listing names models "models/<id>".
            return httpx.Response(200, json={"data": [{"id": f"models/{_GEMINI_EMBED}"}]})
        if request.url.path.endswith("/embeddings"):
            body = json.loads(request.content)
            texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
            width = body.get("dimensions") if self.honour and body.get("dimensions") else None
            width = width or self.native
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"object": "embedding", "index": i, "embedding": [0.01] * width}
                        for i in range(len(texts))
                    ],
                    "model": body["model"],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                },
            )
        return httpx.Response(404, text="not found")

    def embedding_bodies(self) -> list[dict[str, Any]]:
        return [
            json.loads(r.content) for r in self.requests if r.url.path.endswith("/embeddings")
        ]

    def client_factory(self) -> Callable[..., httpx.AsyncClient]:
        def _factory(**kwargs: Any) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.MockTransport(self.handler), timeout=kwargs.get("timeout", 30)
            )

        return _factory


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _Endpoint]:
    def _make(native: int, **kw: Any) -> _Endpoint:
        server = _Endpoint(native, **kw)
        monkeypatch.setattr(
            "app.ai_router.model_endpoints.endpoint_http_client", server.client_factory()
        )
        return server

    return _make


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _register(model_id: str = _GEMINI_EMBED, **extra: Any) -> ModelEndpoint:
    m = ModelEndpoint(
        provider="gemini",
        model_id=model_id,
        display_name=model_id,
        capabilities=[_EM],
        base_url=_GEMINI_BASE,
        extra={"source": "override", **extra},
    )
    model_registry.register_configured(m)
    return m


def _client(monkeypatch: pytest.MonkeyPatch, index_dim: int = 1536) -> TestClient:
    from app.core.config import get_settings

    monkeypatch.setenv("EMBEDDING_DIM", str(index_dim))
    get_settings.cache_clear()
    set_model_registry_store(ModelRegistryStore(_FakeRedis()))
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _ADMIN_CTX if key == "ak_admin" else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(models_router)
    return TestClient(app)


def _embedding_row(client: TestClient, model_id: str = _GEMINI_EMBED) -> dict[str, Any]:
    out = client.get("/models/configured", headers=_ADMIN).json()
    (group,) = [g for g in out["capabilities"] if g["capability"] == "embedding"]
    (row,) = [r for r in group["models"] if r["model_id"] == model_id]
    return row


_SAVE = {
    "provider": "gemini",
    "model_id": _GEMINI_EMBED,
    "base_url": _GEMINI_BASE,
    "capabilities": ["embedding"],
}


# ── POST / GET round-trip ────────────────────────────────────────────────────


def test_output_dimensions_round_trip_keep_and_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    store = get_model_registry_store()
    assert store is not None

    r = client.post("/models/configured", headers=_ADMIN, json={**_SAVE, "output_dimensions": 1536})
    assert r.status_code == 200, r.text
    assert store.get("gemini", _GEMINI_EMBED)["output_dimensions"] == 1536
    row = _embedding_row(client)
    assert row["output_dimensions"] == 1536
    # The requested width is what the dimension check uses: it fits the 1536-d index.
    assert row["dimensions"] == 1536 and row["dimension_mismatch"] is False

    # An edit that leaves the field out (an older client) keeps it.
    r = client.post("/models/configured", headers=_ADMIN, json={**_SAVE, "cost_per_1k_input": 1})
    assert r.status_code == 200
    assert store.get("gemini", _GEMINI_EMBED)["output_dimensions"] == 1536

    # A string number from a form is accepted.
    client.post("/models/configured", headers=_ADMIN, json={**_SAVE, "output_dimensions": "768"})
    assert store.get("gemini", _GEMINI_EMBED)["output_dimensions"] == 768

    # null clears it: the model is back to its native (catalog) 3072-d width.
    client.post("/models/configured", headers=_ADMIN, json={**_SAVE, "output_dimensions": None})
    assert "output_dimensions" not in store.get("gemini", _GEMINI_EMBED)
    row = _embedding_row(client)
    assert row["output_dimensions"] is None
    assert row["dimensions"] == 3072 and row["dimension_mismatch"] is True


@pytest.mark.parametrize("bad", [-1, 8193, "abc", True, 1.5, [1536]])
def test_invalid_output_dimensions_are_refused(monkeypatch: pytest.MonkeyPatch, bad: Any) -> None:
    client = _client(monkeypatch)
    r = client.post("/models/configured", headers=_ADMIN, json={**_SAVE, "output_dimensions": bad})
    assert r.status_code == 400, r.text
    assert "output_dimensions" in r.json()["detail"]


def test_output_dimensions_upper_bound_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    r = client.post("/models/configured", headers=_ADMIN, json={**_SAVE, "output_dimensions": 8192})
    assert r.status_code == 200, r.text


def test_output_dimensions_is_for_embedding_models_only(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch)
    body = {**_SAVE, "model_id": "gemini-2.5-flash", "capabilities": ["text_generation"]}
    r = client.post("/models/configured", headers=_ADMIN, json={**body, "output_dimensions": 768})
    assert r.status_code == 400
    assert "embedding models only" in r.json()["detail"]


def test_saving_a_new_width_drops_a_measurement_taken_at_another_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(monkeypatch)
    store = get_model_registry_store()
    assert store is not None
    # "Test connection" ran before without a requested width: 3072-d measured.
    store.record_dimension(_GEMINI_EMBED, _GEMINI_BASE, 3072)

    client.post("/models/configured", headers=_ADMIN, json={**_SAVE, "output_dimensions": 1536})
    saved = store.get("gemini", _GEMINI_EMBED)
    assert "dimensions" not in saved  # stale: measured again on first use
    assert _embedding_row(client)["dimension_mismatch"] is False

    # A measurement OF the requested width is kept.
    store.record_dimension(_GEMINI_EMBED, _GEMINI_BASE, 1536)
    client.post("/models/configured", headers=_ADMIN, json={**_SAVE, "output_dimensions": 1536})
    assert store.get("gemini", _GEMINI_EMBED)["dimensions"] == 1536


# ── Test connection sends ``dimensions`` ─────────────────────────────────────


def test_test_connection_sends_dimensions_with_a_bearer_key(
    monkeypatch: pytest.MonkeyPatch, endpoint: Callable[..., _Endpoint]
) -> None:
    server = endpoint(3072)
    client = _client(monkeypatch, index_dim=1536)
    body = {**_SAVE, "api_key": "typed-once", "output_dimensions": 1536}

    out = client.post("/models/configured/test-endpoint", headers=_ADMIN, json=body).json()

    assert out["ok"] is True and out["probe"] == "embedding", out
    assert out["model_listed"] is True  # "models/gemini-embedding-001" counts
    assert out["dimensions"] == 1536 and out["index_dimension"] == 1536
    assert out["requested_dimensions"] == 1536
    assert out["dimension_mismatch"] is False and out["dimensions_ignored"] is False
    assert server.embedding_bodies() == [
        {"model": _GEMINI_EMBED, "input": ["ping"], "dimensions": 1536}
    ]
    embed_call = next(r for r in server.requests if r.url.path.endswith("/embeddings"))
    assert str(embed_call.url) == f"{_GEMINI_BASE}/embeddings"
    assert embed_call.headers["authorization"] == "Bearer typed-once"
    assert "typed-once" not in json.dumps(out)


def test_test_connection_without_a_width_sends_none_and_flags_the_mismatch(
    monkeypatch: pytest.MonkeyPatch, endpoint: Callable[..., _Endpoint]
) -> None:
    server = endpoint(3072)
    client = _client(monkeypatch, index_dim=1536)
    out = client.post(
        "/models/configured/test-endpoint", headers=_ADMIN, json={**_SAVE, "api_key": "k"}
    ).json()
    assert server.embedding_bodies() == [{"model": _GEMINI_EMBED, "input": ["ping"]}]
    assert out["dimensions"] == 3072 and out["dimension_mismatch"] is True
    assert out["requested_dimensions"] is None and out["dimensions_ignored"] is False


def test_test_connection_uses_the_saved_width_and_reports_an_ignored_one(
    monkeypatch: pytest.MonkeyPatch, endpoint: Callable[..., _Endpoint]
) -> None:
    server = endpoint(3072, honour_dimensions=False)
    client = _client(monkeypatch, index_dim=1536)
    client.post("/models/configured", headers=_ADMIN, json={**_SAVE, "output_dimensions": 1536})

    out = client.post(
        "/models/configured/test-endpoint", headers=_ADMIN, json={**_SAVE, "api_key": "k"}
    ).json()

    assert server.embedding_bodies()[0]["dimensions"] == 1536
    assert out["requested_dimensions"] == 1536
    assert out["dimensions"] == 3072 and out["dimensions_ignored"] is True
    assert out["dimension_mismatch"] is True
    assert "ignored the dimensions parameter" in out["detail"]


# ── Registry embedder sends ``dimensions``; the dimension check accepts it ───


async def test_registry_embedder_requests_the_output_width(
    endpoint: Callable[..., _Endpoint],
) -> None:
    from app.ai_router.model_endpoints import encrypt_endpoint_api_key

    server = endpoint(3072)
    _register(output_dimensions=1536, api_key_encrypted=encrypt_endpoint_api_key("gk"))
    model_registry.set_preferences({"embedding": [f"gemini/{_GEMINI_EMBED}"]})

    resolution = resolve_embedder(_settings(embedding_dim=1536))

    # Native 3072-d (catalog) but asked for 1536: accepted for the 1536-d index.
    assert resolution.source == "registry", resolution.registry_refusal
    assert resolution.model == _GEMINI_EMBED and resolution.dimension == 1536
    resp = await resolution.embedder.embed(EmbedRequest(texts=["q"], input_type="query"))
    assert len(resp.embeddings[0]) == 1536
    vectors = await resolution.embedder.embed_batch(["a", "b"])
    assert [len(v) for v in vectors] == [1536, 1536]
    bodies = server.embedding_bodies()
    assert [b["dimensions"] for b in bodies] == [1536, 1536]
    assert all(b["model"] == _GEMINI_EMBED for b in bodies)
    embed_call = next(r for r in server.requests if r.url.path.endswith("/embeddings"))
    assert embed_call.headers["authorization"] == "Bearer gk"


def test_without_output_dimensions_the_native_width_is_refused() -> None:
    _register()
    model_registry.set_preferences({"embedding": [f"gemini/{_GEMINI_EMBED}"]})
    resolution = resolve_embedder(_settings(embedding_dim=1536))
    assert resolution.source != "registry"
    assert "3072-d" in resolution.registry_refusal and "1536-d" in resolution.registry_refusal


def test_an_output_width_that_differs_from_the_index_is_refused() -> None:
    _register(output_dimensions=768)
    model_registry.set_preferences({"embedding": [f"gemini/{_GEMINI_EMBED}"]})
    resolution = resolve_embedder(_settings(embedding_dim=1536))
    assert resolution.source != "registry"
    assert "768-d" in resolution.registry_refusal


def test_a_measured_width_wins_over_the_requested_one() -> None:
    """An endpoint that ignored ``dimensions`` was measured at its native width."""
    from app.providers.registry_embedder import embedding_model_dimension

    _register(output_dimensions=1536, dimensions=3072)
    assert embedding_model_dimension("gemini", _GEMINI_EMBED) == 3072


# ── Providers ────────────────────────────────────────────────────────────────


def test_openai_compatible_sends_dimensions_only_for_its_embed_model() -> None:
    from app.providers.openai_compatible import OpenAICompatibleProvider

    p = OpenAICompatibleProvider(
        api_key="k", base_url=_GEMINI_BASE, embed_model=_GEMINI_EMBED, embed_dimensions=768
    )
    assert p._embed_kwargs("document", _GEMINI_EMBED) == {"dimensions": 768}
    assert p._embed_kwargs("document", "another-embed-model") == {}
    plain = OpenAICompatibleProvider(api_key="k", base_url=_GEMINI_BASE, embed_model="m")
    assert plain._embed_kwargs("document", "m") == {}


def test_native_gemini_embedder_gets_the_output_width(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.registry_embedder import build_registry_model_embedder

    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    m = ModelEndpoint(
        provider="gemini",
        model_id=_GEMINI_EMBED,
        display_name=_GEMINI_EMBED,
        capabilities=[_EM],
        extra={"source": "override", "output_dimensions": 768},
    )
    embedder = build_registry_model_embedder(m, _settings())
    assert embedder._embed_dimensions == 768


async def test_native_gemini_embed_sends_output_dimensionality_only_when_set() -> None:
    from types import SimpleNamespace

    from app.providers.gemini_provider import GeminiProvider

    calls: list[dict[str, Any]] = []

    async def _embed_content(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(embeddings=[SimpleNamespace(values=[0.1, 0.2])])

    def _provider(dims: int | None) -> GeminiProvider:
        p = GeminiProvider.__new__(GeminiProvider)
        p._client = SimpleNamespace(  # type: ignore[assignment]
            aio=SimpleNamespace(models=SimpleNamespace(embed_content=_embed_content))
        )
        p._types = SimpleNamespace(EmbedContentConfig=dict)  # type: ignore[assignment]
        p._embed_model = _GEMINI_EMBED
        p._embed_dimensions = dims
        return p

    await _provider(768).embed(EmbedRequest(texts=["q"], input_type="query"))
    await _provider(None).embed(EmbedRequest(texts=["d"]))
    assert calls[0]["config"] == {"task_type": "RETRIEVAL_QUERY", "output_dimensionality": 768}
    assert calls[1]["config"] == {"task_type": "RETRIEVAL_DOCUMENT"}
