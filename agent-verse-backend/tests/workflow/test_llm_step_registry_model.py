"""Workflow LLM Prompt node: the chosen model resolves through the Model Registry.

The builder's Model dropdown was hard-coded (GPT-4o, Claude 3.5, Gemini 1.5 …)
and the step sent that slug to whatever provider the run had. The dropdown now
lists the configured registry models; the step resolves the chosen one through
the registry dispatch (its own endpoint / key), and an unknown model fails the
step with a clear error.
"""

# Isolate ambient provider/model env (keys) so only the registry configures LLMs.
_ISOLATE_PROVIDER_ENV = True

from collections.abc import Iterator
from typing import Any

import pytest

from app.ai_router.registry import model_registry
from app.providers.base import CompletionResponse
from app.workflow.context import ContextResolver
from app.workflow.dsl import StepDefinition
from app.workflow.steps import StepServiceUnavailableError
from tests.providers._registry_llm_server import (
    DEFAULT_REPLY,
    MODEL,
    MODEL_KEY,
    LocalLLMServer,
    add_registry_model,
)

TENANT = "tenant-wf-registry"


class _NoByok:
    async def get_config(self, tenant_id: str, *, strict: bool = False) -> None:
        return None


class _Recording:
    def __init__(self, name: str, *, byok: bool = False) -> None:
        self.name = name
        self.models: list[str] = []
        if byok:
            self._byok_tenant_id = TENANT

    async def complete(self, request: Any) -> CompletionResponse:
        self.models.append(request.model)
        return CompletionResponse(content=f'{{"by": "{self.name}"}}', model=request.model,
                                  input_tokens=1, output_tokens=1)


@pytest.fixture
def llm_server() -> Iterator[LocalLLMServer]:
    server = LocalLLMServer().start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import app.ai_router.registry_store as store_mod
    import app.ai_router.selection as sel
    from app.providers import guarded_completion, registry_llm
    from app.scaling import tasks

    async def _no_charge(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(guarded_completion, "_charge", _no_charge)
    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(store_mod, "_store", None)
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    model_registry.clear_configured()
    registry_llm.reset_shared_registry_provider()
    tasks._reset_worker_deployment_provider()
    yield
    model_registry.clear_configured()
    registry_llm.reset_shared_registry_provider()
    tasks._reset_worker_deployment_provider()


def _state() -> dict[str, Any]:
    return {
        "run_id": "run-reg",
        "tenant_id": TENANT,
        "inputs": {"text": "hello"},
        "step_outputs": {},
        "vars": {},
        "is_test_run": False,
    }


def _node(model: str | None, **services: Any) -> Any:
    from app.workflow.steps.llm_step import LLMStepNode

    return LLMStepNode(
        StepDefinition(id="l1", type="llm", prompt="{{inputs.text}}", model=model),
        ContextResolver(),
        **services,
    )


def _platform_resolver() -> Any:
    from app.providers.llm_resolution import TenantLLMProviderResolver, platform_llm_provider

    return TenantLLMProviderResolver(platform_provider=platform_llm_provider(), store=_NoByok())


async def test_chosen_registry_model_is_called_at_its_endpoint_with_its_key(
    llm_server: LocalLLMServer,
) -> None:
    add_registry_model(llm_server.url)
    add_registry_model("http://127.0.0.1:9/v1", model_id="other-model")
    out = await _node(MODEL, llm_provider_resolver=_platform_resolver()).execute(_state())
    assert out["step_outputs"]["l1"] == {"result": DEFAULT_REPLY}
    (call,) = llm_server.requests
    assert call.model == MODEL
    assert call.authorization == f"Bearer {MODEL_KEY}"


async def test_default_registry_order_when_no_model_is_chosen(
    llm_server: LocalLLMServer,
) -> None:
    add_registry_model(llm_server.url)
    await _node(None, llm_provider_resolver=_platform_resolver()).execute(_state())
    assert [r.model for r in llm_server.requests] == [MODEL]


async def test_unknown_model_fails_the_step_clearly(llm_server: LocalLLMServer) -> None:
    add_registry_model(llm_server.url)
    node = _node("gpt-4o", llm_provider_resolver=_platform_resolver())
    with pytest.raises(StepServiceUnavailableError, match="not a configured text-generation"):
        await node.execute(_state())
    assert llm_server.requests == []


async def test_registry_model_is_dispatched_even_on_an_env_platform_provider(
    llm_server: LocalLLMServer,
) -> None:
    """An env platform provider (wrapped in the dispatch) still sends the
    registry model to its own endpoint."""
    from app.providers.model_dispatch import ModelDispatchProvider

    add_registry_model(llm_server.url)
    platform = _Recording("platform")
    node = _node(MODEL, llm_provider=ModelDispatchProvider(platform))
    await node.execute(_state())
    assert platform.models == []
    assert [r.model for r in llm_server.requests] == [MODEL]


async def test_a_tenants_own_provider_keeps_serving_its_own_model_names() -> None:
    byok = _Recording("tenant", byok=True)
    out = await _node("tenant-private-model", llm_provider=byok).execute(_state())
    assert byok.models == ["tenant-private-model"]
    assert out["step_outputs"]["l1"] == {"by": "tenant"}
