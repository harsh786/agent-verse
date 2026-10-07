"""BUG B, no cluster access: a Model Registry embedding model fixes "embedding provider
not configured" for ingestion.

On the owner's cluster no embedding env var was set (no NVIDIA_API_KEY /
NVIDIA_EMBED_MODEL / EMBEDDING_BASE_URL ...), so every ingestion path answered 503
"Embedding provider is unavailable: embedding provider not configured". Besides the
chart settings, an operator can register an embedding model in the Model Registry
with its own base_url + vault-encrypted API key from the UI. This proves that, with
NO embedding env vars at all, such an entry — NVIDIA's hosted
``nvidia/nemotron-3-embed-1b`` (2048-d) at https://integrate.api.nvidia.com/v1 —
becomes the embedder the worker's ingestion uses, and that the 503 reason names
both fixes.

HTTP is answered in-process (``httpx.MockTransport``); the public host's DNS
lookup is stubbed, nothing leaves the process.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.ai_router.registry_store import ModelRegistryStore
from app.core.config import Settings
from app.providers.embedder_factory import EmbedderResolution, resolve_embedder
from tests.providers.test_registry_embedder_base_url import _EMBED_ENV, _Endpoint, _FakeRedis

_ISOLATE_PROVIDER_ENV = True

NVIDIA_URL = "https://integrate.api.nvidia.com/v1"
NEMOTRON = "nvidia/nemotron-3-embed-1b"
_ALL_EMBED_ENV = (
    *_EMBED_ENV,
    "NVIDIA_EMBED_MODEL",
    "NVIDIA_EMBED_DIM",
    "NVIDIA_BASE_URL",
    "ONPREM_ENABLED",
    "ONPREM_EMBEDDING_BASE_URL",
)


@pytest.fixture(autouse=True)
def _no_embedding_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    import app.ai_router.registry_store as rs
    import app.ai_router.selection as sel
    import app.net.ssrf_guard as guard

    for name in _ALL_EMBED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(rs, "_store", ModelRegistryStore(_FakeRedis()))
    real_resolve = guard._resolve_host

    def _resolve(host: str) -> list[str]:  # NVIDIA's public API host, without real DNS
        return ["34.117.59.81"] if host == "integrate.api.nvidia.com" else real_resolve(host)

    monkeypatch.setattr(guard, "_resolve_host", _resolve)
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


def _bare_settings() -> Settings:
    """Settings as on the cluster: no embedding configuration whatsoever."""
    return Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize("provider", ["nvidia", "openai_compatible"])
async def test_a_registry_nemotron_model_is_the_ingestion_embedder_without_env(
    monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    from app.ai_router.model_endpoints import encrypt_endpoint_api_key

    settings = _bare_settings()
    # Precondition: env alone configures nothing — the 503 the owner saw.
    assert resolve_embedder(settings).status == "not_configured"

    server = _Endpoint(2048, models=(NEMOTRON,))
    monkeypatch.setattr(
        "app.ai_router.model_endpoints.endpoint_http_client", server.client_factory()
    )
    model_registry.register_configured(
        ModelEndpoint(
            provider=provider,
            model_id=NEMOTRON,
            display_name="Nemotron embed",
            capabilities=[ModelCapability.EMBEDDING],
            base_url=NVIDIA_URL,
            extra={
                "source": "override",
                "api_key_encrypted": encrypt_endpoint_api_key("test-nvidia-registry-key"),
                "dimensions": 2048,
            },
        )
    )
    # The worker ingestion path resolves with these settings (get_settings()).
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)

    from app.ingestion.worker_services import build_worker_knowledge_services

    monkeypatch.setattr(
        "app.ingestion.worker_services.bind_worker_guardrail_rules", lambda _db: None
    )
    monkeypatch.setattr("app.embedding.usage.configure_usage_redis_from_env", lambda: None)
    store, embedder = build_worker_knowledge_services(lambda: None)

    assert embedder is not None, "ingestion still has no embedder"
    assert store._embedding_dim == 2048  # the index is sized to the registry model
    vectors = await embedder.embed_batch(["chunk one", "chunk two"])
    assert [len(v) for v in vectors] == [2048, 2048]
    (call,) = server.embedding_calls()
    assert str(call.url) == f"{NVIDIA_URL}/embeddings"
    assert json.loads(call.content)["model"] == NEMOTRON
    assert call.headers["authorization"] == "Bearer test-nvidia-registry-key"

    resolution = resolve_embedder(settings)
    assert resolution.source == "registry"
    assert resolution.model == NEMOTRON
    assert resolution.dimension == 2048


def test_the_not_configured_reason_names_both_fixes() -> None:
    reason = EmbedderResolution().reason()
    assert "NVIDIA_API_KEY" in reason and "NVIDIA_EMBED_MODEL" in reason
    assert "EMBEDDING_BASE_URL" in reason
    assert "Model Registry" in reason


async def test_an_api_started_without_an_embedder_binds_a_later_registry_model() -> None:
    """The API resolves once at startup; with no embedder then, a model registered
    later (from the UI) is bound when the shared registry version changes — no
    restart, i.e. no cluster access, needed."""
    from app.providers.embedder_factory import watch_for_late_registry_embedder

    state: dict[str, Any] = {"embedder": None, "version": 1, "rebinds": 0}

    def _rebind() -> None:
        state["rebinds"] += 1
        if state["version"] >= 2:  # the operator saved the embedding model
            state["embedder"] = object()

    def _bump_after_two_polls() -> int:
        state["polls"] = state.get("polls", 0) + 1
        if state["polls"] >= 3:
            state["version"] = 2
        return int(state["version"])

    await watch_for_late_registry_embedder(
        _rebind,
        is_bound=lambda: state["embedder"] is not None,
        version=_bump_after_two_polls,
        interval_s=0,
    )
    assert state["embedder"] is not None
    assert state["rebinds"] == 1  # only when the registry changed, not every poll
