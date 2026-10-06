"""ModelDispatchProvider: a registry model of another provider reaches that provider.

The platform resolves one provider; a Groq / Claude / OpenAI / … model picked by
the Model Registry used to be sent to it (wrong API). Overrides now dispatch to
their own provider; everything else still goes to the wrapped provider.
"""

# Isolate ambient provider env (keys) for deterministic readiness.
_ISOLATE_PROVIDER_ENV = True

from dataclasses import dataclass
from typing import Any

import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.providers.base import CompletionRequest, Message
from app.providers.fake import FakeProvider
from app.providers.model_dispatch import ModelDispatchProvider, with_model_dispatch

_TG = ModelCapability.TEXT_GENERATION


@dataclass
class _Resp:
    content: str
    model: str


class _Named:
    def __init__(self, name: str, provider_type: str = "", endpoints: Any = None) -> None:
        self.name = name
        self.calls: list[str] = []
        self._agentverse_provider_type = provider_type
        if endpoints is not None:
            self._endpoints = endpoints

    async def complete(self, request: Any) -> _Resp:
        self.calls.append(request.model)
        return _Resp(content=self.name, model=request.model)

    async def embed(self, request: Any) -> str:
        return f"embedded-by-{self.name}"


def _register(provider: str, model_id: str, source: str) -> None:
    model_registry.register_configured(
        ModelEndpoint(provider=provider, model_id=model_id, display_name=model_id,
                      capabilities=[_TG], extra={"source": source})
    )


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    model_registry.clear_configured()
    yield
    model_registry.clear_configured()


def _req(model: str) -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="hi")], model=model)


@pytest.mark.asyncio
async def test_override_model_of_another_provider_goes_to_that_provider(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    _register("groq", "llama-3.1-8b-instant", "override")
    base, groq = _Named("nvidia", "nvidia"), _Named("groq")
    built: list[Any] = []

    def _instantiate(cfg):
        built.append(cfg)
        return groq

    monkeypatch.setattr("app.providers.registry._instantiate_provider", _instantiate)
    p = ModelDispatchProvider(base)
    resp = await p.complete(_req("llama-3.1-8b-instant"))
    assert resp.content == "groq"
    assert built[0].provider_type == "groq"
    assert built[0].base_url == "https://api.groq.com/openai/v1"
    # the adapter is built once and reused
    await p.complete(_req("llama-3.1-8b-instant"))
    assert len(built) == 1 and base.calls == []


@pytest.mark.asyncio
async def test_env_seeded_and_unknown_models_stay_on_the_platform_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    _register("openai", "env-model", "env")
    base = _Named("nvidia", "nvidia")
    p = ModelDispatchProvider(base)
    assert (await p.complete(_req("env-model"))).content == "nvidia"
    assert (await p.complete(_req("never-registered"))).content == "nvidia"
    assert (await p.complete(_req(""))).content == "nvidia"


@pytest.mark.asyncio
async def test_models_the_cluster_serves_stay_on_the_cluster(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    _register("nvidia", "nvidia/nemotron", "override")
    base = _Named("cluster", "onprem", endpoints={"nvidia/nemotron": object()})
    p = ModelDispatchProvider(base)
    assert (await p.complete(_req("nvidia/nemotron"))).content == "cluster"


@pytest.mark.asyncio
async def test_same_provider_override_stays_on_the_platform_provider(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    _register("nvidia", "meta/llama-3.3-70b-instruct", "override")
    base = _Named("nvidia", "nvidia")
    p = ModelDispatchProvider(base)
    assert (await p.complete(_req("meta/llama-3.3-70b-instruct"))).content == "nvidia"


@pytest.mark.asyncio
async def test_provider_without_credentials_is_not_dispatched(monkeypatch):
    _register("anthropic", "claude-sonnet-5-5", "override")  # no ANTHROPIC_API_KEY
    base = _Named("nvidia", "nvidia")
    p = ModelDispatchProvider(base)
    assert (await p.complete(_req("claude-sonnet-5-5"))).content == "nvidia"


@pytest.mark.asyncio
async def test_embeddings_and_attributes_belong_to_the_wrapped_provider():
    base = _Named("nvidia", "nvidia")
    p = ModelDispatchProvider(base)
    assert await p.embed(object()) == "embedded-by-nvidia"
    assert p._agentverse_provider_type == "nvidia"
    p._circuit_scope = "t1"  # set after resolution: lands on the wrapped provider
    assert base._circuit_scope == "t1"
    assert p.inner is base


def test_with_model_dispatch_skips_placeholders_and_is_idempotent():
    fake = FakeProvider(responses=["x"])
    assert with_model_dispatch(fake) is fake
    assert with_model_dispatch(None) is None
    wrapped = with_model_dispatch(_Named("n"))
    assert with_model_dispatch(wrapped) is wrapped
