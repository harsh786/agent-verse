"""Embedding side paths all use the Model Registry-configured embedder.

* ``MultiEndpointLLMProvider.embed`` (on-prem / NVIDIA cluster) built its own
  embedder from ``NVIDIA_EMBED_MODEL`` / ``ONPREM_EMBEDDING_MODEL`` → delegates to
  the platform registry embedder (``process_embedder``).
* Chat providers from the env provider registry (``NVIDIA_EMBED_MODEL`` with a
  literal default, ``OLLAMA_EMBED_MODEL``) → delegate too.
* BYOK: a tenant's own provider embeds with the tenant-configured embedding
  model when set, else with the platform registry embedder — never with the
  env-only ``EMBEDDING_MODEL``.
* The resolver names every env-keyed provider's model explicitly (provider
  classes carry no default).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.core.config import Settings
from app.providers.base import EmbedRequest, EmbedResponse
from app.providers.embedder_factory import (
    EmbedderResolution,
    delegate_embeddings_to_platform,
    embedder_model_name,
    process_embedder,
    reset_process_embedder,
    set_process_embedder,
)

_ISOLATE_PROVIDER_ENV = True


class _Registry:
    """The platform's registry embedder (stand-in): records what it embeds."""

    def __init__(self, model: str = "registry-embed") -> None:
        self._embed_model_name = model
        self.requests: list[EmbedRequest] = []

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        self.requests.append(request)
        return EmbedResponse(embeddings=[[0.5, 0.5] for _ in request.texts], model="x")

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.requests.append(EmbedRequest(texts=texts))
        return [[0.5, 0.5] for _ in texts]


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    reset_process_embedder()
    yield
    reset_process_embedder()


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


# ── on-prem / NVIDIA cluster ──────────────────────────────────────────────────


async def test_onprem_cluster_embed_delegates_to_the_registry_embedder() -> None:
    from app.providers.onprem import build_onprem_provider

    provider = build_onprem_provider(
        _settings(
            onprem_enabled=True,
            onprem_qwen_base_url="http://10.0.0.5:30080/v1",
            onprem_embedding_base_url="http://10.0.0.5:30082/v1",
            onprem_embedding_model="Qwen/Qwen3-Embedding-0.6B",
            nvidia_api_key="nvapi-test",
            nvidia_embed_model="nvidia/nemotron-3-embed-1b",
        )
    )
    assert provider is not None
    assert provider._embed is None  # no embedder of its own any more
    registry = _Registry()
    set_process_embedder(lambda: registry)

    out = await provider.embed(EmbedRequest(texts=["q"], model="Qwen/Qwen3.5-4B"))
    vectors = await provider.embed_batch(["a", "b"])

    assert out.embeddings == [[0.5, 0.5]]
    assert vectors == [[0.5, 0.5], [0.5, 0.5]]
    # A caller-named model never reaches the registry embedder (its model decides).
    assert registry.requests[0].model == ""
    assert embedder_model_name(provider) == "registry-embed"


async def test_onprem_cluster_without_a_registry_embedder_refuses() -> None:
    from app.providers.base import EmbedderUnavailableError, embed_texts
    from app.providers.onprem import build_onprem_provider

    provider = build_onprem_provider(
        _settings(onprem_enabled=True, onprem_qwen_base_url="http://10.0.0.5:30080/v1")
    )
    set_process_embedder(lambda: None)
    with pytest.raises(EmbedderUnavailableError):
        await embed_texts(["q"], provider=provider)


async def test_an_injected_embed_provider_still_wins() -> None:
    from app.providers.onprem import MultiEndpointLLMProvider

    injected = _Registry("injected")
    provider = MultiEndpointLLMProvider(
        endpoints={"m": object()},  # type: ignore[dict-item]
        default_model="m",
        embed_provider=injected,  # type: ignore[arg-type]
    )
    set_process_embedder(lambda: _Registry())
    await provider.embed(EmbedRequest(texts=["q"]))
    assert len(injected.requests) == 1


# ── env provider registry (chat providers) ────────────────────────────────────


async def test_env_nvidia_chat_provider_embeds_with_the_registry_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.registry import ProviderConfig, _instantiate_provider

    monkeypatch.setenv("NVIDIA_EMBED_MODEL", "nvidia/some-env-embedder")
    provider = _instantiate_provider(
        ProviderConfig(provider_type="nvidia", api_key="nvapi-test", models=["meta/llama"])
    )
    assert provider is not None
    # The NVIDIA_EMBED_MODEL literal default / env read is gone.
    assert getattr(provider, "_embed_model_name", None) is None
    registry = _Registry()
    set_process_embedder(lambda: registry)
    await provider.embed(EmbedRequest(texts=["q"]))
    assert [r.texts for r in registry.requests] == [["q"]]
    assert embedder_model_name(provider) == "registry-embed"


def test_delegation_keeps_none() -> None:
    assert delegate_embeddings_to_platform(None) is None


# ── BYOK embedding policy ─────────────────────────────────────────────────────


def _tenant_cfg(provider: str = "openai", **extra: Any) -> dict[str, Any]:
    return {
        "provider": provider,
        "encrypted_key": "enc",
        "decrypted_key": "sk-tenant-test",
        "default_model": "gpt-4o-mini" if provider == "openai" else "",
        **extra,
    }


def test_byok_with_a_tenant_embedding_model_embeds_with_it_on_its_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.tenant_provider import build_tenant_provider

    monkeypatch.setenv("EMBEDDING_MODEL", "platform-env-model")  # never used
    provider = build_tenant_provider(
        _tenant_cfg(embedding_model="text-embedding-3-large"), tenant_id="t-byok"
    )
    assert provider._embed_model_name == "text-embedding-3-large"
    assert not getattr(provider, "_agentverse_platform_embedder", False)
    assert embedder_model_name(provider) == "text-embedding-3-large"


async def test_byok_without_a_tenant_embedding_model_uses_the_platform_embedder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.tenant_provider import build_tenant_provider

    monkeypatch.setenv("EMBEDDING_MODEL", "platform-env-model")  # never forced onto it
    provider = build_tenant_provider(_tenant_cfg(), tenant_id="t-byok")
    assert provider._embed_model_name is None
    registry = _Registry()
    set_process_embedder(lambda: registry)
    await provider.embed(EmbedRequest(texts=["q"]))
    assert [r.texts for r in registry.requests] == [["q"]]
    assert embedder_model_name(provider) == "registry-embed"


async def test_byok_anthropic_always_uses_the_platform_embedder() -> None:
    from app.providers.tenant_provider import build_tenant_provider

    provider = build_tenant_provider(
        _tenant_cfg("anthropic", embedding_model="voyage-3.5"), tenant_id="t-byok"
    )
    registry = _Registry()
    set_process_embedder(lambda: registry)
    await provider.embed(EmbedRequest(texts=["q"]))
    assert len(registry.requests) == 1


async def test_byok_loader_no_longer_passes_the_env_embedding_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.providers.tenant_vault as tv
    from app.providers import llm_resolution

    monkeypatch.setenv("EMBEDDING_MODEL", "platform-env-model")

    async def _prepared(cfg: dict[str, Any], tenant_id: str, db: Any) -> dict[str, Any]:
        return cfg

    monkeypatch.setattr(tv, "prepare_tenant_llm_config", _prepared)
    provider = await llm_resolution.abuild_tenant_byok_provider(
        _tenant_cfg(), "t-byok", db_factory=object()
    )
    assert provider._embed_model_name is None
    assert getattr(provider, "_agentverse_platform_embedder", False) is True


# ── the process embedder ──────────────────────────────────────────────────────


def test_process_embedder_is_the_registered_api_embedder() -> None:
    registry = _Registry()
    set_process_embedder(lambda: registry)
    assert process_embedder() is registry


def test_worker_process_embedder_is_resolved_once_and_reloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.providers.embedder_factory as ef

    calls: list[bool] = []
    inner = _Registry("worker-registry-embed")

    def _resolve(settings: Any = None, *, wire_registry_store: bool = False) -> Any:
        calls.append(wire_registry_store)
        return EmbedderResolution(embedder=inner, provider="onprem",
                                  model="worker-registry-embed", source="registry")

    monkeypatch.setattr(ef, "resolve_embedder", _resolve)
    monkeypatch.setattr(ef, "_registry_version", lambda: 1)
    first = process_embedder()
    second = process_embedder()
    assert first is second
    assert calls == [True]  # the shared registry store is wired (same model as the API)
    assert embedder_model_name(first) == "worker-registry-embed"


def test_worker_without_an_embedder_rechecks_only_when_the_registry_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.providers.embedder_factory as ef

    calls: list[int] = []
    version = {"v": 1}
    monkeypatch.setattr(ef, "_PROCESS_RECHECK_S", 0.0)
    monkeypatch.setattr(ef, "_registry_version", lambda: version["v"])

    def _resolve(settings: Any = None, *, wire_registry_store: bool = False) -> Any:
        calls.append(version["v"])
        return EmbedderResolution()

    monkeypatch.setattr(ef, "resolve_embedder", _resolve)
    assert process_embedder() is None
    assert process_embedder() is None
    assert calls == [1]
    version["v"] = 2  # an operator added an embedding model
    assert process_embedder() is None
    assert calls == [1, 2]


# ── the resolver names every model explicitly ────────────────────────────────


def test_resolver_names_the_voyage_model_explicitly() -> None:
    from app.ai_router.model_catalog import deployment_default_embed_model
    from app.providers.embedder_factory import resolve_embedder

    resolution = resolve_embedder(_settings(voyage_api_key="vk-test"))
    assert resolution.provider == "voyage"
    assert resolution.model == deployment_default_embed_model("voyage")
    assert resolution.embedder._model == deployment_default_embed_model("voyage")


def test_resolver_names_the_gemini_model_explicitly() -> None:
    pytest.importorskip("google.genai")
    from app.ai_router.model_catalog import deployment_default_embed_model
    from app.providers.embedder_factory import resolve_embedder

    resolution = resolve_embedder(_settings(google_api_key="AIza-test"))
    assert resolution.provider == "gemini"
    assert resolution.embedder._embed_model == deployment_default_embed_model("gemini")
