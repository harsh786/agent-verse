"""Registry-driven embedder selection and SAME-model endpoint failover.

Vectors of different embedding models are not comparable (even at the same
width), so:

* a saved embedding preference order picks the embedder only when its first
  eligible model is servable here and its width matches ``EMBEDDING_DIM``; a
  mismatch is refused (env order applies) with a logged reason;
* failover goes only between endpoints serving the SAME model id — never to
  another model, even one later in the preference order.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.core.config import Settings
from app.providers.base import EmbedRequest, EmbedResponse
from app.providers.embedder_factory import resolve_embedder
from app.providers.registry_embedder import (
    SameModelFailoverEmbedder,
    embedding_dimension_status,
    embedding_model_dimension,
)

_ISOLATE_PROVIDER_ENV = True

_EM = ModelCapability.EMBEDDING
_QWEN = "Qwen/Qwen3-Embedding-0.6B"
_EMBED_ENV = (
    "OPENAI_API_KEY",
    "VOYAGE_API_KEY",
    "GOOGLE_API_KEY",
    "NVIDIA_API_KEY",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL",
    "EMBEDDING_API_KEY",
    "SENTENCE_TRANSFORMERS_MODEL",
    "OPENAI_BASE_URL",
    "EMBEDDING_FAILOVER_TIMEOUT_S",
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Any:
    import app.ai_router.selection as sel

    for name in _EMBED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def _add(provider: str, model_id: str, cost: float = 0.0) -> None:
    model_registry.register_configured(
        ModelEndpoint(
            provider=provider,
            model_id=model_id,
            display_name=model_id,
            capabilities=[_EM],
            cost_per_1k_input=cost,
            extra={"source": "env"},  # deployment-configured: always eligible
        )
    )


class _FakeLocal:
    def __init__(self, model_name: str = "all-mpnet-base-v2") -> None:
        self._model_name = model_name
        self.embedding_dim = 768


class _FakeEndpoint:
    """An embedding endpoint that records calls and answers or raises."""

    def __init__(self, label: str, *, fail: bool = False, hang: bool = False) -> None:
        self.label = label
        self.fail = fail
        self.hang = hang
        self.models: list[str] = []
        self.batches = 0

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        self.models.append(request.model)
        if self.hang:
            await asyncio.sleep(10)
        if self.fail:
            raise ConnectionError(f"{self.label} endpoint down")
        return EmbedResponse(
            embeddings=[[float(len(self.label))] for _ in request.texts], model=request.model
        )

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batches += 1
        if self.fail:
            raise TimeoutError(f"{self.label} timed out")
        return [[float(len(self.label))] for _ in texts]


def _fake_builder(endpoints: dict[str, _FakeEndpoint], built: list[tuple[str, str]]) -> Any:
    def _build(provider: str, model_id: str, settings: Any) -> Any:
        built.append((provider, model_id))
        return endpoints[provider]

    return _build


# ── Selection ────────────────────────────────────────────────────────────────


def test_without_a_saved_order_the_env_order_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    _add("nvidia", "nvidia/nemotron-3-embed-1b")
    s = _settings(sentence_transformers_model="all-mpnet-base-v2", nvidia_api_key="test-k")
    with patch("app.providers.voyage_provider.LocalEmbedProvider", _FakeLocal):
        resolution = resolve_embedder(s)
    assert isinstance(resolution.embedder, _FakeLocal)
    assert resolution.provider == "sentence_transformers"
    assert resolution.source == "env"
    assert resolution.registry_refusal == ""


def test_saved_order_builds_the_preferred_models_provider() -> None:
    _add("openai", "text-embedding-3-small", cost=0.0)  # cheaper, but not preferred
    _add("nvidia", "nvidia/nemotron-3-embed-1b", cost=0.1)
    model_registry.set_preferences({"embedding": ["nvidia/nvidia/nemotron-3-embed-1b"]})
    s = _settings(
        sentence_transformers_model="all-mpnet-base-v2",
        nvidia_api_key="test-nvidia-key",
        embedding_dim=2048,
    )
    with patch("app.providers.voyage_provider.LocalEmbedProvider", _FakeLocal):
        resolution = resolve_embedder(s)
    from app.providers.openai_compatible import OpenAICompatibleProvider

    assert resolution.source == "registry"
    assert resolution.provider == "nvidia"
    assert resolution.model == "nvidia/nemotron-3-embed-1b"
    assert resolution.dimension == 2048
    assert isinstance(resolution.embedder, OpenAICompatibleProvider)
    assert resolution.embedder._embed_model_name == "nvidia/nemotron-3-embed-1b"
    assert "nvidia.com" in resolution.embedder._base_url


def test_dimension_mismatch_is_refused_with_a_reason_and_env_order_applies() -> None:
    _add("openai", "text-embedding-3-small")  # 1536-d
    model_registry.set_preferences({"embedding": ["openai/text-embedding-3-small"]})
    s = _settings(
        sentence_transformers_model="all-mpnet-base-v2",
        openai_api_key="test-openai-key",
        embedding_dim=2048,
    )
    log = MagicMock()
    with (
        patch("app.providers.voyage_provider.LocalEmbedProvider", _FakeLocal),
        patch("app.providers.registry_embedder.logger", log),
    ):
        resolution = resolve_embedder(s)
    # The env order: Voyage/OpenAI/... — OpenAI's env path is keyed, so it wins.
    assert resolution.source == "env"
    assert "1536" in resolution.registry_refusal and "2048" in resolution.registry_refusal
    refused = [
        c for c in log.warning.call_args_list if c.args[0] == "embedding_registry_model_refused"
    ]
    assert refused and "re-index" in refused[0].kwargs["reason"]


def test_preferred_model_without_credentials_falls_back_to_env() -> None:
    _add("voyage", "voyage-3.5")
    model_registry.set_preferences({"embedding": ["voyage/voyage-3.5"]})
    s = _settings(sentence_transformers_model="all-mpnet-base-v2", embedding_dim=1024)
    with patch("app.providers.voyage_provider.LocalEmbedProvider", _FakeLocal):
        resolution = resolve_embedder(s)
    assert isinstance(resolution.embedder, _FakeLocal)
    assert "VOYAGE_API_KEY" in resolution.registry_refusal


def test_endpoints_of_the_same_model_become_the_failover_chain() -> None:
    _add("onprem", _QWEN)
    _add("nvidia", _QWEN, cost=0.1)
    _add("voyage", "voyage-3.5")  # another model: never a failover target
    model_registry.set_preferences({"embedding": [f"onprem/{_QWEN}", "voyage/voyage-3.5"]})
    endpoints = {p: _FakeEndpoint(p) for p in ("onprem", "nvidia", "voyage")}
    built: list[tuple[str, str]] = []
    with patch(
        "app.providers.registry_embedder.build_endpoint_embedder",
        _fake_builder(endpoints, built),
    ):
        resolution = resolve_embedder(_settings(embedding_dim=1024))
    assert resolution.source == "registry"
    assert isinstance(resolution.embedder, SameModelFailoverEmbedder)
    assert resolution.endpoints == ["onprem", "nvidia"]
    assert built == [("onprem", _QWEN), ("nvidia", _QWEN)]
    assert resolution.model == _QWEN


# ── Same-model failover ──────────────────────────────────────────────────────


async def test_primary_endpoint_failure_fails_over_to_the_same_model_elsewhere() -> None:
    primary, secondary = _FakeEndpoint("onprem", fail=True), _FakeEndpoint("nvidia")
    embedder = SameModelFailoverEmbedder(_QWEN, [("onprem", primary), ("nvidia", secondary)])
    log = MagicMock()
    with patch("app.providers.registry_embedder.logger", log):
        resp = await embedder.embed(EmbedRequest(texts=["a", "b"], input_type="query"))
    assert resp.embeddings == [[6.0], [6.0]]  # answered by "nvidia"
    # Both endpoints were asked for the SAME model id.
    assert primary.models == [_QWEN] and secondary.models == [_QWEN]
    failover = [c for c in log.warning.call_args_list if c.args[0] == "embedding_failover"]
    assert failover and failover[0].kwargs["failed_endpoint"] == "onprem"
    assert failover[0].kwargs["next_endpoint"] == "nvidia"
    assert failover[0].kwargs["model"] == _QWEN


async def test_batch_embedding_fails_over_too() -> None:
    primary, secondary = _FakeEndpoint("onprem", fail=True), _FakeEndpoint("nvidia")
    embedder = SameModelFailoverEmbedder(_QWEN, [("onprem", primary), ("nvidia", secondary)])
    assert await embedder.embed_batch(["x"]) == [[6.0]]
    assert primary.batches == 1 and secondary.batches == 1


async def test_a_hanging_endpoint_times_out_and_fails_over() -> None:
    primary, secondary = _FakeEndpoint("onprem", hang=True), _FakeEndpoint("nvidia")
    embedder = SameModelFailoverEmbedder(
        _QWEN, [("onprem", primary), ("nvidia", secondary)], timeout_s=0.05
    )
    resp = await embedder.embed(EmbedRequest(texts=["a"]))
    assert resp.embeddings == [[6.0]]


async def test_a_failed_endpoint_is_tried_last_while_cooling_down() -> None:
    now = [0.0]
    primary, secondary = _FakeEndpoint("onprem", fail=True), _FakeEndpoint("nvidia")
    embedder = SameModelFailoverEmbedder(
        _QWEN, [("onprem", primary), ("nvidia", secondary)], cooldown_s=30, clock=lambda: now[0]
    )
    await embedder.embed(EmbedRequest(texts=["a"]))
    await embedder.embed(EmbedRequest(texts=["a"]))
    assert len(primary.models) == 1  # skipped first on the second call
    now[0] = 31.0
    primary.fail = False
    await embedder.embed(EmbedRequest(texts=["a"]))
    assert len(primary.models) == 2


async def test_a_different_model_is_never_used_as_failover() -> None:
    """The preferred model's only endpoint fails: the call fails — the next model
    of the preference order (another vector space) is never built or called."""
    _add("nvidia", "nvidia/nemotron-3-embed-1b")
    _add("openai", "text-embedding-3-large")
    model_registry.set_preferences(
        {"embedding": ["nvidia/nvidia/nemotron-3-embed-1b", "openai/text-embedding-3-large"]}
    )
    endpoints = {"nvidia": _FakeEndpoint("nvidia", fail=True), "openai": _FakeEndpoint("openai")}
    built: list[tuple[str, str]] = []
    with patch(
        "app.providers.registry_embedder.build_endpoint_embedder",
        _fake_builder(endpoints, built),
    ):
        resolution = resolve_embedder(_settings(embedding_dim=2048))
    assert resolution.source == "registry"
    assert built == [("nvidia", "nvidia/nemotron-3-embed-1b")]
    with pytest.raises(ConnectionError):
        await resolution.embedder.embed(EmbedRequest(texts=["a"]))
    assert endpoints["openai"].models == []


async def test_every_endpoint_failing_raises_and_keeps_the_model() -> None:
    a, b = _FakeEndpoint("onprem", fail=True), _FakeEndpoint("nvidia", fail=True)
    embedder = SameModelFailoverEmbedder(_QWEN, [("onprem", a), ("nvidia", b)])
    with pytest.raises(ConnectionError):
        await embedder.embed(EmbedRequest(texts=["a"]))
    assert a.models == [_QWEN] and b.models == [_QWEN]


async def test_an_explicitly_requested_other_model_is_not_failed_over() -> None:
    a, b = _FakeEndpoint("onprem", fail=True), _FakeEndpoint("nvidia")
    embedder = SameModelFailoverEmbedder(_QWEN, [("onprem", a), ("nvidia", b)])
    with pytest.raises(ConnectionError):
        await embedder.embed(EmbedRequest(texts=["a"], model="some-other-model"))
    assert b.models == []


# ── Dimensions ───────────────────────────────────────────────────────────────


def test_known_dimensions_and_the_declared_width_of_the_deployments_model() -> None:
    assert embedding_model_dimension("openai", "text-embedding-3-small") == 1536
    assert embedding_model_dimension("voyage", "voyage-3.5-lite") == 1024
    assert embedding_model_dimension("ollama", "nomic-embed-text") == 768
    assert embedding_model_dimension("custom", "never-heard-of-it") is None
    s = _settings(nvidia_embed_model="nvidia/some-new-embedder", nvidia_embed_dim=4096)
    assert embedding_model_dimension("nvidia", "nvidia/some-new-embedder", s) == 4096
    status = embedding_dimension_status("gemini", "gemini-embedding-001", 2048)
    assert status["dimensions"] == 3072 and status["dimension_mismatch"] is True
