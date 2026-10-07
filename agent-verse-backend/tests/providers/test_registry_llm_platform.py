"""Registry-only deployments: the Model Registry's models ARE the platform LLM.

Owner report: LLM models were configured ONLY in the Model Registry (own
``base_url`` + vault-encrypted per-model key; no OPENAI/ANTHROPIC/NVIDIA key in
the server env) and agent goals ran on the canned FakeProvider. The platform
resolved its provider from env keys only, fell back to the placeholder, and the
registry dispatch was never built on top of it.

These tests use a REAL local OpenAI-compatible HTTP server.
"""

# Isolate ambient provider/model env (keys) so only the registry configures LLMs.
_ISOLATE_PROVIDER_ENV = True

from collections.abc import Iterator
from typing import Any

import pytest

from app.ai_router.registry import model_registry
from app.providers.base import CompletionRequest, Message
from tests.providers._registry_llm_server import (
    DEFAULT_REPLY,
    LocalLLMServer,
    add_registry_model,
    find_registry,
)

from tests.providers._registry_llm_server import MODEL as _MODEL
from tests.providers._registry_llm_server import MODEL_KEY as _KEY


@pytest.fixture
def llm_server() -> Iterator[LocalLLMServer]:
    server = LocalLLMServer().start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import app.ai_router.registry_store as store_mod
    import app.ai_router.selection as sel
    from app.providers import registry_llm
    from app.scaling import tasks

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(store_mod, "_store", None)
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    model_registry.clear_configured()
    model_registry.set_preferences({})
    registry_llm.reset_shared_registry_provider()
    tasks._reset_worker_deployment_provider()
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})
    registry_llm.reset_shared_registry_provider()
    tasks._reset_worker_deployment_provider()


def _req(model: str = "") -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="hi")], model=model)


# ── resolution ──────────────────────────────────────────────────────────────


def test_registry_only_platform_provider_is_the_registry_dispatch(
    llm_server: LocalLLMServer,
) -> None:
    from app.providers.llm_resolution import is_placeholder_provider, platform_llm_provider
    from app.providers.registry import resolve_provider
    from app.providers.registry_llm import RegistryLLMProvider

    add_registry_model(llm_server.url)
    provider = resolve_provider()
    assert isinstance(provider, RegistryLLMProvider)
    assert not is_placeholder_provider(provider)
    assert provider._default_model == _MODEL
    assert isinstance(platform_llm_provider(), RegistryLLMProvider)


async def test_a_call_without_a_model_goes_to_the_registry_endpoint_with_its_key(
    llm_server: LocalLLMServer,
) -> None:
    from app.providers.registry import resolve_provider

    add_registry_model(llm_server.url)
    resp = await resolve_provider().complete(_req(""))
    assert resp.content == DEFAULT_REPLY
    (call,) = llm_server.requests
    assert call.model == _MODEL
    assert call.authorization == f"Bearer {_KEY}"


async def test_an_env_default_model_nothing_serves_is_rerouted_to_the_registry(
    llm_server: LocalLLMServer,
) -> None:
    """DEFAULT_MODEL=gpt-4o with no OpenAI key: the registry model answers."""
    from app.providers.registry import resolve_provider

    add_registry_model(llm_server.url)
    await resolve_provider().complete(_req("gpt-4o"))
    assert [r.model for r in llm_server.requests] == [_MODEL]


async def test_goal_planner_executor_and_verifier_call_the_registry_model(
    llm_server: LocalLLMServer,
) -> None:
    from app.agent.graph import AgentGraph
    from app.agent.state import GoalStatus
    from app.providers.registry import resolve_provider
    from app.tenancy.context import PlanTier, TenantContext

    add_registry_model(llm_server.url)
    provider = resolve_provider()
    graph = AgentGraph(planner=provider, executor=provider, verifier=provider)
    state = await graph.run(
        goal="Say hello",
        tenant_ctx=TenantContext(tenant_id="reg-t1", plan=PlanTier.PROFESSIONAL,
                                 api_key_id="k1"),
    )
    assert state.status == GoalStatus.COMPLETE
    assert {"planner", "executor", "verifier"} <= llm_server.roles()
    assert {r.model for r in llm_server.requests} == {_MODEL}
    assert {r.authorization for r in llm_server.requests} == {f"Bearer {_KEY}"}


def test_api_goal_loop_uses_the_registry_when_the_app_provider_is_a_placeholder(
    llm_server: LocalLLMServer,
) -> None:
    """GoalService (API path): app.state._app_provider is the startup placeholder."""
    from types import SimpleNamespace

    from app.governance.audit import AuditLog
    from app.governance.hitl import HITLGateway
    from app.providers.fake import FakeProvider
    from app.providers.registry_llm import RegistryLLMProvider
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    add_registry_model(llm_server.url)
    state = SimpleNamespace(_app_provider=FakeProvider(responses=["canned"]))
    svc = GoalService(audit_log=AuditLog(), hitl=HITLGateway())
    loop = svc._make_agent_loop_for_tenant(
        TenantContext(tenant_id="reg-t2", plan=PlanTier.FREE, api_key_id="k2"), state
    )
    assert isinstance(find_registry(loop._planner), RegistryLLMProvider)
    assert loop._simulated_provider is False


async def test_workflow_resolver_uses_the_registry(llm_server: LocalLLMServer) -> None:
    from app.providers.llm_resolution import TenantLLMProviderResolver, platform_llm_provider

    class _NoByok:
        async def get_config(self, tenant_id: str, strict: bool = False) -> None:
            return None

    add_registry_model(llm_server.url)
    resolver = TenantLLMProviderResolver(platform_provider=platform_llm_provider(),
                                         store=_NoByok())
    provider = await resolver("tenant-wf")
    await provider.complete(_req(""))
    assert [r.model for r in llm_server.requests] == [_MODEL]


# ── late binding ────────────────────────────────────────────────────────────


async def test_a_model_added_after_startup_is_used_without_a_restart(
    llm_server: LocalLLMServer,
) -> None:
    from app.providers.llm_resolution import (
        NoLLMProviderConfiguredError,
        TenantLLMProviderResolver,
        is_placeholder_provider,
        platform_llm_provider,
    )
    from app.providers.registry_llm import RegistryLLMProvider

    class _NoByok:
        async def get_config(self, tenant_id: str, strict: bool = False) -> None:
            return None

    live = platform_llm_provider()  # "startup": nothing configured anywhere
    assert isinstance(live, RegistryLLMProvider)
    assert is_placeholder_provider(live)
    resolver = TenantLLMProviderResolver(platform_provider=live, store=_NoByok())
    with pytest.raises(NoLLMProviderConfiguredError, match="Model Registry"):
        await resolver("tenant-late")

    add_registry_model(llm_server.url)  # the operator adds a model

    assert not is_placeholder_provider(live)
    assert await resolver("tenant-late") is live
    await live.complete(_req(""))
    assert [r.model for r in llm_server.requests] == [_MODEL]


def test_api_app_binds_the_registry_provider_and_goals_pick_up_late_models(
    llm_server: LocalLLMServer,
) -> None:
    from app.governance.audit import AuditLog
    from app.governance.hitl import HITLGateway
    from app.main import create_app
    from app.providers.fake import FakeProvider
    from app.providers.registry_llm import RegistryLLMProvider
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    app = create_app()
    assert isinstance(app.state._app_provider, FakeProvider)  # dev, nothing configured
    assert isinstance(app.state.platform_llm, RegistryLLMProvider)

    add_registry_model(llm_server.url)
    svc = GoalService(audit_log=AuditLog(), hitl=HITLGateway())
    loop = svc._make_agent_loop_for_tenant(
        TenantContext(tenant_id="reg-t3", plan=PlanTier.FREE, api_key_id="k3"), app.state
    )
    assert loop._simulated_provider is False


async def test_late_bind_watcher_binds_once_the_registry_changes() -> None:
    from app.providers.registry_llm import watch_for_late_registry_llm

    versions = iter([1, 1, 2, 2, 2])
    bound: list[bool] = []

    def _bind() -> None:
        bound.append(True)

    await watch_for_late_registry_llm(
        _bind, is_bound=lambda: bool(bound), version=lambda: next(versions), interval_s=0
    )
    assert bound == [True]


# ── nothing configured ──────────────────────────────────────────────────────


async def test_production_with_nothing_configured_fails_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.llm_resolution import NoLLMProviderConfiguredError, platform_llm_provider

    monkeypatch.setenv("ENVIRONMENT", "production")
    provider = platform_llm_provider()
    with pytest.raises(NoLLMProviderConfiguredError, match="add a model in the Model Registry"):
        await provider.complete(_req(""))


def test_production_api_goal_with_nothing_configured_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from app.governance.audit import AuditLog
    from app.governance.hitl import HITLGateway
    from app.providers.llm_resolution import UnconfiguredLLMProvider
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    monkeypatch.setenv("ENVIRONMENT", "production")
    svc = GoalService(audit_log=AuditLog(), hitl=HITLGateway())
    with pytest.raises(RuntimeError, match="add a model in the Model Registry"):
        svc._make_agent_loop_for_tenant(
            TenantContext(tenant_id="none-t", plan=PlanTier.FREE, api_key_id="k"),
            SimpleNamespace(_app_provider=UnconfiguredLLMProvider()),
        )


def test_production_start_guard_accepts_a_registry_only_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai_router.registry_store import ModelRegistryStore, set_model_registry_store
    from app.core.config import Settings
    from app.main import _resolve_provider_for_app

    class _Redis:
        def __init__(self, entries: str) -> None:
            self.data = {"model_registry:configured": entries}

        def get(self, k: str) -> Any:
            return self.data.get(k)

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("LLM_REQUIRE_PLATFORM_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No LLM provider configured for production"):
        _resolve_provider_for_app(Settings())
    set_model_registry_store(ModelRegistryStore(_Redis(
        '[{"provider": "openai_compatible", "model_id": "q", "capabilities": '
        '["text_generation"], "base_url": "http://10.0.0.5:8000/v1"}]'
    )))
    _resolve_provider_for_app(Settings())  # starts: goals use the registry


async def test_development_with_nothing_configured_keeps_a_visible_fake(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.providers.llm_resolution import platform_llm_provider

    provider = platform_llm_provider()
    assert provider.serves_fake()
    resp = await provider.complete(_req(""))
    assert resp.content  # canned answer, development only


# ── own key without a base_url ──────────────────────────────────────────────


async def test_a_model_with_its_own_key_and_no_url_uses_its_providers_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai_router.selection import is_eligible
    from app.providers.registry import resolve_provider

    built: list[Any] = []

    class _Groq:
        async def complete(self, request: Any) -> Any:
            return type("R", (), {"content": "groq", "model": request.model})()

    def _instantiate(cfg: Any) -> Any:
        built.append(cfg)
        return _Groq()

    add_registry_model(None, model_id="llama-3.3-70b", provider="groq", key="gsk-own")
    entry = model_registry.list_configured()[0]
    assert is_eligible(entry)  # it used to be "No API key — skipped at runtime"
    monkeypatch.setattr("app.providers.registry._instantiate_provider", _instantiate)
    resp = await resolve_provider().complete(_req(""))
    assert resp.content == "groq"
    assert built[-1].provider_type == "groq" and built[-1].api_key == "gsk-own"
    assert built[-1].models == ["llama-3.3-70b"]
