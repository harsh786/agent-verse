"""BYOK-3: workflow LLM steps use the tenant's own key (tenant BYOK → platform → error).

Workflows got one process-wide provider at worker start: a tenant's BYOK key was
never used, and with no platform key the step answered with FakeProvider's
canned text. Now every LLM-using step resolves the provider per run with the
same resolver as goals, fails with "no LLM provider configured for tenant" when
there is none, and FakeProvider is never selected outside development/test.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.providers import vault as vault_mod
from app.providers.base import CompletionResponse
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.steps import StepServiceUnavailableError

TENANT = "tenant-byok3"
TENANT_KEY = "sk-tenant-own-key-123456789"


def _state(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "run-1",
        "tenant_id": TENANT,
        "inputs": {"text": "hello"},
        "step_outputs": {},
        "vars": {},
        "is_test_run": False,
    }
    base.update(kw)
    return base


class _RecordingProvider:
    """Stands in for the provider class build_tenant_provider would construct."""

    def __init__(self, pname: str, api_key: str, model: str, base_url: str | None) -> None:
        self.pname, self.api_key, self.model, self.base_url = pname, api_key, model, base_url
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> CompletionResponse:
        self.requests.append(request)
        return CompletionResponse(
            content='{"answer": "from tenant model"}',
            model=request.model or self.model,
            input_tokens=7,
            output_tokens=5,
        )


class _Store:
    def __init__(self, config: dict[str, Any] | None) -> None:
        self.config = config
        self.strict_reads = 0

    async def get_config(self, tenant_id: str, *, strict: bool = False) -> dict[str, Any] | None:
        assert tenant_id == TENANT
        self.strict_reads += int(strict)
        return self.config


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[_RecordingProvider]:
    from app.providers import tenant_provider

    made: list[_RecordingProvider] = []

    def _construct(pname: str, api_key: str, model: str, base_url: Any, embed: Any) -> Any:
        provider = _RecordingProvider(pname, api_key, model, base_url)
        made.append(provider)
        return provider

    monkeypatch.setattr(tenant_provider, "_construct", _construct)
    return made


def _tenant_config() -> dict[str, Any]:
    vault = vault_mod.get_vault()
    return {
        "provider": "anthropic",
        "encrypted_key": vault.encrypt(TENANT_KEY),
        "model": "claude-tenant-model",
        "vault_key_fingerprint": vault.fingerprint(),
    }


def _llm_node(**services: Any) -> Any:
    from app.workflow.steps.llm_step import LLMStepNode

    return LLMStepNode(
        StepDefinition(id="l1", type="llm", prompt="{{inputs.text}}"),
        ContextResolver(),
        **services,
    )


async def test_llm_step_uses_the_tenant_byok_key_and_charges_the_tenant(
    monkeypatch: pytest.MonkeyPatch, built: list[_RecordingProvider]
) -> None:
    from app.providers import guarded_completion
    from app.providers.fake import FakeProvider
    from app.providers.llm_resolution import TenantLLMProviderResolver

    charges: list[dict[str, Any]] = []

    async def _charge(scope: Any, tenant: Any, **kw: Any) -> None:
        charges.append({"tenant": getattr(tenant, "tenant_id", None), **kw})

    monkeypatch.setattr(guarded_completion, "_charge", _charge)
    store = _Store(_tenant_config())
    platform = FakeProvider(responses=["PLATFORM CANNED"])
    node = _llm_node(
        llm_provider=platform,  # the old process-wide provider must NOT be used
        llm_provider_resolver=TenantLLMProviderResolver(
            platform_provider=platform, store=store, db_factory=object()
        ),
    )
    out = await node.execute(_state())

    assert len(built) == 1
    provider = built[0]
    assert provider.pname == "anthropic"
    assert provider.api_key == TENANT_KEY  # the tenant's decrypted key
    assert provider.model == "claude-tenant-model"
    assert len(provider.requests) == 1
    # No step model → the tenant's model, not the platform default slug.
    assert provider.requests[0].model in ("", None)
    assert out["step_outputs"]["l1"] == {"answer": "from tenant model"}
    assert platform.call_history == []
    assert store.strict_reads == 1
    # Charged to the run's tenant, under the run id — like a goal's LLM calls.
    assert charges and charges[0]["tenant"] == TENANT
    assert charges[0]["goal_id"] == "workflow:run-1"
    assert charges[0]["role"] == "workflow_llm_step"


async def test_llm_step_without_tenant_or_platform_key_fails_honestly() -> None:
    from app.providers.fake import FakeProvider
    from app.providers.llm_resolution import TenantLLMProviderResolver

    node = _llm_node(
        llm_provider_resolver=TenantLLMProviderResolver(
            platform_provider=FakeProvider(responses=["canned"]), store=_Store(None)
        )
    )
    with pytest.raises(StepServiceUnavailableError, match="no LLM provider configured for tenant"):
        await node.execute(_state())


async def test_llm_step_falls_back_to_the_platform_provider_without_byok() -> None:
    from app.providers.llm_resolution import TenantLLMProviderResolver

    platform = _RecordingProvider("platform", "platform-key", "platform-model", None)
    node = _llm_node(
        llm_provider_resolver=TenantLLMProviderResolver(
            platform_provider=platform, store=_Store(None)
        )
    )
    out = await node.execute(_state())
    assert len(platform.requests) == 1
    assert out["step_outputs"]["l1"] == {"answer": "from tenant model"}


async def test_broken_tenant_byok_fails_the_step_not_falls_back_to_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.llm_resolution import TenantLLMProviderResolver

    cfg = _tenant_config()
    cfg["encrypted_key"] = vault_mod.CredentialVault("some-other-key-" + "x" * 30).encrypt("k")
    platform = _RecordingProvider("platform", "platform-key", "platform-model", None)
    node = _llm_node(
        llm_provider_resolver=TenantLLMProviderResolver(
            platform_provider=platform, store=_Store(cfg)
        )
    )
    with pytest.raises(StepServiceUnavailableError, match="could not be decrypted"):
        await node.execute(_state())
    assert platform.requests == []


async def test_rag_step_resolves_the_tenant_provider_too(
    built: list[_RecordingProvider],
) -> None:
    from app.providers.llm_resolution import TenantLLMProviderResolver
    from app.workflow.steps.rag_step import RAGStepNode

    class _Knowledge:
        async def retrieve(self, **kw: Any) -> list[dict[str, Any]]:
            self.embedder = kw["embedder"]
            return [{"content": "ctx"}]

    knowledge = _Knowledge()
    registry_embedder = object()  # the platform's Model Registry embedder
    node = RAGStepNode(
        StepDefinition(id="r1", type="rag", prompt="q", input={"collection": "c"}),
        ContextResolver(),
        knowledge_store=knowledge,
        llm_provider_resolver=TenantLLMProviderResolver(store=_Store(_tenant_config())),
        embedder=registry_embedder,
    )
    await node.execute(_state())
    assert built and built[0].api_key == TENANT_KEY  # the tenant's provider completes
    # ...but the query is embedded in the COLLECTION's vector space (its bound
    # embedder, else the registry embedder) — never with the chat provider, whose
    # model is not the one the index was built with.
    assert knowledge.embedder is registry_embedder
    assert knowledge.embedder is not built[0]


async def test_ocr_and_rpa_steps_get_the_tenant_provider(
    built: list[_RecordingProvider],
) -> None:
    from app.providers.llm_resolution import TenantLLMProviderResolver
    from app.workflow.steps.ocr_step import OcrStepNode
    from app.workflow.steps.rpa_step import RPAStepNode

    resolver = TenantLLMProviderResolver(store=_Store(_tenant_config()))
    ocr = OcrStepNode(
        StepDefinition(id="o1", type="ocr"), ContextResolver(), llm_provider_resolver=resolver
    )
    rpa = RPAStepNode(
        StepDefinition(id="p1", type="rpa"), ContextResolver(), llm_provider_resolver=resolver
    )
    assert (await ocr._step_provider(_state())).api_key == TENANT_KEY
    assert (await rpa._step_provider(_state())).api_key == TENANT_KEY


# ── worker / API wiring ───────────────────────────────────────────────────────


def test_worker_runner_wires_the_tenant_resolver_not_a_process_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.llm_resolution import TenantLLMProviderResolver
    from app.workflow import celery_tasks

    monkeypatch.setattr(celery_tasks, "_WORKER_RUNNER", None)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("VAULT_MASTER_KEY", "k" * 40)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    vault_mod._cached_vault.cache_clear()
    try:
        runner = celery_tasks._build_worker_runner()
        services = runner._compiler.services
    finally:
        monkeypatch.setattr(celery_tasks, "_WORKER_RUNNER", None)
        vault_mod._cached_vault.cache_clear()
    assert isinstance(services.get("llm_provider_resolver"), TenantLLMProviderResolver)
    from app.providers.fake import FakeProvider

    for key in ("llm_provider", "provider"):
        assert (
            not isinstance(services.get(key), FakeProvider)
            or type(services.get(key)).__name__ == "UnconfiguredLLMProvider"
        )


# ── FakeProvider never selected outside development/test ──────────────────────


@pytest.fixture
def _no_platform_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.registry as registry

    monkeypatch.setattr(registry, "_detect_providers", lambda: [])


@pytest.mark.parametrize("env", ["production", "staging", "qa"])
async def test_registry_never_returns_canned_fake_outside_dev(
    monkeypatch: pytest.MonkeyPatch, env: str, _no_platform_keys: None
) -> None:
    from app.providers.base import CompletionRequest, Message
    from app.providers.llm_resolution import (
        NoLLMProviderConfiguredError,
        UnconfiguredLLMProvider,
    )
    from app.providers.registry import resolve_provider

    monkeypatch.setenv("ENVIRONMENT", env)
    provider = resolve_provider()
    assert isinstance(provider, UnconfiguredLLMProvider)
    with pytest.raises(NoLLMProviderConfiguredError, match="no LLM provider configured"):
        await provider.complete(
            CompletionRequest(messages=[Message(role="user", content="x")], model="")
        )


@pytest.mark.parametrize("env", ["development", "test"])
def test_registry_fake_only_in_explicit_dev(
    monkeypatch: pytest.MonkeyPatch, env: str, _no_platform_keys: None
) -> None:
    from app.providers.fake import FakeProvider
    from app.providers.llm_resolution import UnconfiguredLLMProvider
    from app.providers.registry import resolve_provider

    monkeypatch.setenv("ENVIRONMENT", env)
    provider = resolve_provider()
    assert isinstance(provider, FakeProvider)
    assert not isinstance(provider, UnconfiguredLLMProvider)


def test_api_app_provider_is_never_canned_fake_in_staging(
    monkeypatch: pytest.MonkeyPatch, _no_platform_keys: None
) -> None:
    from app.core.config import get_settings
    from app.main import _resolve_provider_for_app
    from app.providers.llm_resolution import UnconfiguredLLMProvider

    monkeypatch.setenv("ENVIRONMENT", "staging")
    provider = _resolve_provider_for_app(get_settings())
    assert isinstance(provider, UnconfiguredLLMProvider)


def test_api_goal_loop_refuses_to_simulate_in_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    import app.core.config as config_mod
    from app.services import goal_service

    monkeypatch.setattr(config_mod, "get_settings", lambda: SimpleNamespace(environment="staging"))
    with pytest.raises(RuntimeError, match="FakeProvider"):
        goal_service._make_agent_loop()


async def test_nl_trigger_parse_uses_the_tenant_byok_provider(
    built: list[_RecordingProvider], monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from app.providers import guarded_completion
    from app.providers.llm_resolution import TenantLLMProviderResolver
    from app.workflow.nl_trigger import NLTriggerResolver

    async def _no_charge(*a: Any, **kw: Any) -> None:
        return None

    monkeypatch.setattr(guarded_completion, "_charge", _no_charge)
    resolver = NLTriggerResolver(
        llm_provider=None,
        llm_provider_resolver=TenantLLMProviderResolver(store=_Store(_tenant_config())),
    )
    await resolver.resolve("when the moon is full", tenant_ctx=SimpleNamespace(tenant_id=TENANT))
    assert built and built[0].api_key == TENANT_KEY and len(built[0].requests) == 1
