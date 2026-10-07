"""An embedding model configured in the Model Registry with its OWN endpoint.

Root cause covered here: a registry entry's ``base_url`` worked for the chat
roles (per-model dispatch) but was ignored for embeddings: the embedder for an
``onprem`` model was built from ``ONPREM_EMBEDDING_BASE_URL`` (env), so a vLLM
embedding server registered at its own URL could never be selected.

* precedence: saved embedding order (on the entry's base_url) > env embedder >
  (no env embedder) an operator-added registry model with its own base_url;
* the endpoint passes the egress policy (private networks per
  ``ALLOW_PRIVATE_NETWORK_ACCESS``; metadata never) and uses the entry's
  vault-encrypted credential;
* dimension safety: a measured / known width that differs from the index is
  refused up front; an unknown width is checked on the first response and a
  mismatch raises ``EmbeddingDimensionError`` (no vector returned);
* "Test connection" for an embedding model calls ``/v1/embeddings``, reports
  and records the width;
* the API process re-resolves its embedder when the shared registry changes.

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
from app.ai_router.registry_store import ModelRegistryStore, set_model_registry_store
from app.api.model_registry import router as models_router
from app.core.config import Settings
from app.providers.base import EmbedRequest
from app.providers.embedder_factory import (
    EmbedderResolution,
    RegistryReloadingEmbedder,
    resolve_embedder,
)
from app.providers.registry_embedder import DimensionCheckedEmbedder, SameModelFailoverEmbedder
from app.rag.store import EmbeddingDimensionError
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

_ISOLATE_PROVIDER_ENV = True

_EM = ModelCapability.EMBEDDING
_QWEN = "Qwen/Qwen3-Embedding-0.6B"  # 1024-d (catalog)
_UNKNOWN = "acme/embed-unlisted"  # width unknown until measured
_LAN = "http://192.168.63.104:30082/v1"
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
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(rs, "_store", ModelRegistryStore(_FakeRedis()))
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _register(
    model_id: str,
    *,
    base_url: str | None = _LAN,
    provider: str = "onprem",
    source: str = "override",
    **extra: Any,
) -> ModelEndpoint:
    m = ModelEndpoint(
        provider=provider,
        model_id=model_id,
        display_name=model_id,
        capabilities=[_EM],
        base_url=base_url,
        extra={"source": source, **extra},
    )
    model_registry.register_configured(m)
    return m


class _Endpoint:
    """A tiny OpenAI-compatible embeddings server answered in-process."""

    def __init__(self, dim: int, models: tuple[str, ...] = (_QWEN,)) -> None:
        self.dim = dim
        self.models = models
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in self.models]})
        if request.url.path.endswith("/embeddings"):
            body = json.loads(request.content)
            texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"object": "embedding", "index": i, "embedding": [0.01] * self.dim}
                        for i in range(len(texts))
                    ],
                    "model": body["model"],
                    "usage": {"prompt_tokens": 1, "total_tokens": 1},
                },
            )
        return httpx.Response(404, text="not found")

    def embedding_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith("/embeddings")]

    def client_factory(self) -> Callable[..., httpx.AsyncClient]:
        def _factory(**kwargs: Any) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.MockTransport(self.handler), timeout=kwargs.get("timeout", 30)
            )

        return _factory


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], _Endpoint]:
    def _make(dim: int) -> _Endpoint:
        server = _Endpoint(dim)
        monkeypatch.setattr(
            "app.ai_router.model_endpoints.endpoint_http_client", server.client_factory()
        )
        return server

    return _make


def _primary(embedder: Any) -> Any:
    """The provider object of the primary endpoint under the registry wrappers."""
    assert isinstance(embedder, DimensionCheckedEmbedder)
    inner = embedder._inner
    if isinstance(inner, SameModelFailoverEmbedder):
        return inner._endpoints[0][1]
    return inner


# ── Resolution precedence ────────────────────────────────────────────────────


async def test_preferred_model_is_embedded_at_its_own_base_url_not_the_env_endpoint(
    endpoint: Callable[[int], _Endpoint],
) -> None:
    """The root cause: the on-prem env embedding URL used to win over the entry's."""
    server = endpoint(1024)
    _register(_QWEN)
    model_registry.set_preferences({"embedding": [f"onprem/{_QWEN}"]})
    s = _settings(
        onprem_enabled=True,
        onprem_qwen_base_url="http://10.0.0.5:30080/v1",
        onprem_embedding_base_url="http://10.0.0.5:30082/v1",
        embedding_dim=1024,
    )

    resolution = resolve_embedder(s)

    assert resolution.source == "registry"
    assert resolution.model == _QWEN
    assert resolution.provider == "onprem"  # a name, never a URL (/health is public)
    assert resolution.endpoints[0] == "onprem@192.168.63.104:30082"
    assert str(_primary(resolution.embedder)._client.base_url).rstrip("/") == _LAN
    resp = await resolution.embedder.embed(EmbedRequest(texts=["hello"]))
    assert len(resp.embeddings[0]) == 1024
    (call,) = server.embedding_calls()
    assert str(call.url) == f"{_LAN}/embeddings"
    assert json.loads(call.content)["model"] == _QWEN


def test_without_a_saved_order_the_env_embedder_wins_over_a_registry_endpoint() -> None:
    _register(_QWEN)
    s = _settings(
        embedding_base_url="http://10.1.1.1:9000/v1", embedding_model="env-embed", embedding_dim=1024
    )
    resolution = resolve_embedder(s)
    assert resolution.source == "env"
    assert resolution.provider == "dedicated"
    assert resolution.model == "env-embed"


async def test_no_env_embedder_falls_back_to_the_registry_endpoint_model(
    endpoint: Callable[[int], _Endpoint],
) -> None:
    server = endpoint(1024)
    _register(_QWEN)
    resolution = resolve_embedder(_settings(embedding_dim=1024))
    assert resolution.source == "registry"
    assert resolution.model == _QWEN
    vectors = await resolution.embedder.embed_batch(["a", "b"])
    assert [len(v) for v in vectors] == [1024, 1024]
    assert len(server.embedding_calls()) == 1


def test_registry_endpoint_refused_by_strict_egress_policy_uses_the_env_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "false")
    _register(_QWEN)
    model_registry.set_preferences({"embedding": [f"onprem/{_QWEN}"]})
    s = _settings(
        embedding_base_url="http://10.1.1.1:9000/v1", embedding_model="env-embed", embedding_dim=1024
    )
    resolution = resolve_embedder(s)
    assert resolution.source == "env" and resolution.model == "env-embed"
    assert "no endpoint serving embedding model" in resolution.registry_refusal
    assert any("ALLOW_PRIVATE_NETWORK_ACCESS" in reason for _, reason in resolution.errors)


def test_a_metadata_endpoint_is_never_used() -> None:
    _register(_QWEN, base_url="http://169.254.169.254/latest/v1")
    model_registry.set_preferences({"embedding": [f"onprem/{_QWEN}"]})
    resolution = resolve_embedder(_settings(embedding_dim=1024))
    assert resolution.embedder is None
    assert resolution.registry_refusal


async def test_the_entrys_vault_encrypted_credential_is_sent(
    endpoint: Callable[[int], _Endpoint],
) -> None:
    from app.ai_router.model_endpoints import encrypt_endpoint_api_key

    server = endpoint(1024)
    ciphertext = encrypt_endpoint_api_key("test-embed-key")
    assert "test-embed-key" not in ciphertext
    _register(_QWEN, api_key_encrypted=ciphertext)
    model_registry.set_preferences({"embedding": [f"onprem/{_QWEN}"]})
    resolution = resolve_embedder(_settings(embedding_dim=1024))
    await resolution.embedder.embed(EmbedRequest(texts=["x"]))
    (call,) = server.embedding_calls()
    assert call.headers["authorization"] == "Bearer test-embed-key"


def test_chat_dispatch_uses_the_entrys_credential_too() -> None:
    from app.ai_router.model_endpoints import encrypt_endpoint_api_key
    from app.providers.model_dispatch import ModelDispatchProvider

    model_registry.register_configured(
        ModelEndpoint(
            provider="onprem",
            model_id="Qwen/Qwen3.5-4B",
            display_name="q",
            capabilities=[ModelCapability.TEXT_GENERATION],
            base_url="http://192.168.63.104:30080/v1",
            extra={"source": "override", "api_key_encrypted": encrypt_endpoint_api_key("chat-k")},
        )
    )

    class _Base:
        _agentverse_provider_type = "nvidia"

    target = ModelDispatchProvider(_Base()).target_for("Qwen/Qwen3.5-4B")
    assert target._client.api_key == "chat-k"


# ── Dimension safety ─────────────────────────────────────────────────────────


def test_a_measured_width_that_differs_from_the_index_is_refused_up_front() -> None:
    _register(_UNKNOWN, dimensions=768)
    model_registry.set_preferences({"embedding": [f"onprem/{_UNKNOWN}"]})
    s = _settings(
        embedding_base_url="http://10.1.1.1:9000/v1", embedding_model="env-embed", embedding_dim=1024
    )
    resolution = resolve_embedder(s)
    assert resolution.source == "env"
    assert "768-d" in resolution.registry_refusal and "1024-d" in resolution.registry_refusal
    assert "EMBEDDING_DIM=768" in resolution.registry_refusal


async def test_an_unknown_width_is_checked_on_first_use_and_a_mismatch_refused(
    endpoint: Callable[[int], _Endpoint],
) -> None:
    server = endpoint(768)  # the index is 1024-d
    _register(_UNKNOWN)
    model_registry.set_preferences({"embedding": [f"onprem/{_UNKNOWN}"]})
    resolution = resolve_embedder(_settings(embedding_dim=1024))
    assert resolution.source == "registry" and resolution.dimension is None

    with pytest.raises(EmbeddingDimensionError, match="768-d vectors but the vector index"):
        await resolution.embedder.embed_batch(["doc chunk"])
    # Refused for good: no further call reaches the endpoint, nothing is returned.
    with pytest.raises(EmbeddingDimensionError):
        await resolution.embedder.embed(EmbedRequest(texts=["query"]))
    assert len(server.embedding_calls()) == 1
    # A mismatched width is never recorded as the model's width.
    assert "dimensions" not in (model_registry.get_configured("onprem", _UNKNOWN).extra or {})


async def test_a_matching_unknown_width_is_measured_and_recorded(
    endpoint: Callable[[int], _Endpoint],
) -> None:
    from app.ai_router.registry_store import get_model_registry_store

    endpoint(1024)
    store = get_model_registry_store()
    assert store is not None
    store.upsert(
        {"provider": "onprem", "model_id": _UNKNOWN, "capabilities": ["embedding"],
         "base_url": _LAN}
    )
    _register(_UNKNOWN)
    model_registry.set_preferences({"embedding": [f"onprem/{_UNKNOWN}"]})
    resolution = resolve_embedder(_settings(embedding_dim=1024))

    await resolution.embedder.embed(EmbedRequest(texts=["q"]))

    assert resolution.embedder.embedding_dim == 1024
    assert model_registry.get_configured("onprem", _UNKNOWN).extra["dimensions"] == 1024
    assert store.get("onprem", _UNKNOWN)["dimensions"] == 1024
    assert store.probed_dimension(_UNKNOWN, _LAN) == 1024


# ── Model Registry API: Test connection + save ───────────────────────────────


def _client(monkeypatch: pytest.MonkeyPatch, index_dim: int = 1024) -> TestClient:
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


def test_test_connection_for_an_embedding_model_calls_v1_embeddings_and_records_the_width(
    monkeypatch: pytest.MonkeyPatch, endpoint: Callable[[int], _Endpoint]
) -> None:
    server = endpoint(1024)
    client = _client(monkeypatch)
    body = {"provider": "onprem", "model_id": _UNKNOWN, "base_url": _LAN,
            "capabilities": ["embedding"], "api_key": "typed-once"}

    out = client.post("/models/configured/test-endpoint", headers=_ADMIN, json=body).json()

    assert out["ok"] is True and out["probe"] == "embedding", out
    assert out["dimensions"] == 1024 and out["index_dimension"] == 1024
    assert out["dimension_mismatch"] is False
    (call,) = server.embedding_calls()
    assert str(call.url) == f"{_LAN}/embeddings"
    assert json.loads(call.content) == {"model": _UNKNOWN, "input": ["ping"]}
    assert call.headers["authorization"] == "Bearer typed-once"
    assert "typed-once" not in json.dumps(out)

    # Saving afterwards keeps the measured width with the entry.
    del body["api_key"]
    assert client.post("/models/configured", headers=_ADMIN, json=body).status_code == 200
    from app.ai_router.registry_store import get_model_registry_store

    store = get_model_registry_store()
    assert store is not None and store.get("onprem", _UNKNOWN)["dimensions"] == 1024


def test_test_connection_flags_a_width_that_does_not_fit_the_index(
    monkeypatch: pytest.MonkeyPatch, endpoint: Callable[[int], _Endpoint]
) -> None:
    endpoint(768)
    out = _client(monkeypatch, index_dim=1024).post(
        "/models/configured/test-endpoint",
        headers=_ADMIN,
        json={"provider": "custom", "model_id": _UNKNOWN, "base_url": _LAN,
              "capabilities": ["embedding"]},
    ).json()
    assert out["ok"] is True and out["dimensions"] == 768
    assert out["dimension_mismatch"] is True and "EMBEDDING_DIM=768" in out["dimension_reason"]


def test_saved_credential_is_encrypted_never_returned_and_used_by_the_probe(
    monkeypatch: pytest.MonkeyPatch, endpoint: Callable[[int], _Endpoint]
) -> None:
    from app.ai_router.registry_store import get_model_registry_store

    server = endpoint(1024)
    client = _client(monkeypatch)
    body = {"provider": "onprem", "model_id": _QWEN, "base_url": _LAN,
            "capabilities": ["embedding"], "api_key": "saved-secret"}
    assert client.post("/models/configured", headers=_ADMIN, json=body).status_code == 200

    store = get_model_registry_store()
    assert store is not None
    raw = json.dumps(store.list())
    assert "saved-secret" not in raw and store.get("onprem", _QWEN)["api_key_encrypted"]
    listing = client.get("/models/configured", headers=_ADMIN).json()
    assert "saved-secret" not in json.dumps(listing)
    assert "api_key_encrypted" not in json.dumps(listing)
    row = next(m for g in listing["capabilities"] for m in g["models"] if m["model_id"] == _QWEN)
    assert row["has_api_key"] is True

    # An edit without api_key keeps it; the probe uses the saved credential.
    del body["api_key"]
    assert client.post("/models/configured", headers=_ADMIN, json=body).status_code == 200
    assert store.get("onprem", _QWEN)["api_key_encrypted"]
    out = client.post("/models/configured/test-endpoint", headers=_ADMIN, json=body).json()
    assert out["ok"] is True
    assert server.embedding_calls()[-1].headers["authorization"] == "Bearer saved-secret"


def test_model_test_button_probes_an_embedding_model_with_an_embedding(
    monkeypatch: pytest.MonkeyPatch, endpoint: Callable[[int], _Endpoint]
) -> None:
    server = endpoint(1024)
    client = _client(monkeypatch)
    _register(_UNKNOWN)
    out = client.post(
        "/models/test", headers=_ADMIN, json={"provider": "onprem", "model_id": _UNKNOWN}
    ).json()
    assert out["status"] == "ok" and out["probe"] == "embedding", out
    assert out["dimensions"] == 1024
    assert len(server.embedding_calls()) == 1
    assert model_registry.get_configured("onprem", _UNKNOWN).extra["dimensions"] == 1024


# ── The API process follows registry changes ────────────────────────────────


class _Named:
    def __init__(self, name: str) -> None:
        self._embed_model_name = name
        self.calls = 0

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[1.0] for _ in texts]


async def test_reloading_embedder_swaps_when_the_registry_version_changes() -> None:
    old, new = _Named("env-embed"), _Named(_QWEN)
    versions = iter([1, 1, 2])
    resolved: list[str] = []

    def _resolve() -> EmbedderResolution:
        resolved.append("x")
        return EmbedderResolution(embedder=new, provider="onprem", model=_QWEN, source="registry")

    changes: list[str] = []
    proxy = RegistryReloadingEmbedder(
        EmbedderResolution(embedder=old, provider="dedicated", model="env-embed"),
        resolve=_resolve,
        check_interval_s=0.0,
        version=lambda: next(versions),
    )
    proxy.add_change_listener(lambda r: changes.append(r.model))

    await proxy.embed_batch(["a"])  # version unchanged: no re-resolution
    assert (old.calls, new.calls, resolved) == (1, 0, [])
    await proxy.embed_batch(["b"])  # version bumped: re-resolved and swapped
    assert (old.calls, new.calls) == (1, 1)
    assert changes == [_QWEN] and proxy.resolution.model == _QWEN
    from app.observability.traced_provider import unwrap_provider

    assert unwrap_provider(proxy) is new
