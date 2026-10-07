"""Every fixed reasoning path runs on the Model Registry's model — registry-only.

A deployment that configures its LLM ONLY in the Model Registry (an
operator-added OpenAI-compatible model with its own ``base_url`` and saved key,
nothing in env) must have each formerly-hardcoded path call THAT model: the NL
scheduler (``claude-opus-4-8``), the NL trigger parser (``gpt-4o``), the org
strategic brief (always ``claude-sonnet-4-5``), team formation, the guardrail
judge (``gpt-4o-mini``), tool self-heal (``claude-haiku-3-5``), the org
collaboration tick (``gpt-4o-mini``), RAG strategy / indexing LLMs, chat
(model list + preferred model), the self-optimizer's routing recommendation and
``GET /models/active``.

The model is served by a REAL local OpenAI-compatible HTTP server — the test
stands in only at the network edge; registry, resolver, dispatch, provider
adapter and HTTP client are the production code.
"""

# Isolate ambient provider/model env (keys) so only the registry configures LLMs.
_ISOLATE_PROVIDER_ENV = True

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest

from app.ai_router.registry import model_registry
from tests.providers._registry_llm_server import (
    MODEL,
    MODEL_KEY,
    LocalLLMServer,
    add_registry_model,
)

TENANT = "tenant-registry-reasoning"


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

    async def _no_charge(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(guarded_completion, "_charge", _no_charge)
    monkeypatch.setattr(guarded_completion, "_platform_services", None)
    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(store_mod, "_store", None)
    monkeypatch.setenv("ALLOW_PRIVATE_NETWORK_ACCESS", "true")
    for var in ("DEFAULT_MODEL", "NVIDIA_MODEL", "OPENAI_MODEL", "DEFAULT_PLANNING_MODEL",
                "DEFAULT_EXECUTION_MODEL", "DEFAULT_VERIFICATION_MODEL"):
        monkeypatch.delenv(var, raising=False)
    model_registry.clear_configured()
    model_registry.set_preferences({})
    registry_llm.reset_shared_registry_provider()
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})
    registry_llm.reset_shared_registry_provider()


@pytest.fixture
def provider(llm_server: LocalLLMServer) -> Any:
    """The platform provider of a registry-only deployment (no env keys)."""
    from app.providers.llm_resolution import UnconfiguredLLMProvider
    from app.providers.registry_llm import registry_backed_provider

    add_registry_model(llm_server.url)
    return registry_backed_provider(UnconfiguredLLMProvider())


def _models(server: LocalLLMServer) -> list[str]:
    return [r.model for r in server.requests]


def _assert_registry_model(server: LocalLLMServer) -> None:
    assert server.requests, "the path never called the registry model"
    assert set(_models(server)) == {MODEL}
    assert {r.authorization for r in server.requests} == {f"Bearer {MODEL_KEY}"}


async def test_nl_scheduler(provider: Any, llm_server: LocalLLMServer) -> None:
    from app.triggers.nl_scheduler import NLScheduler

    await NLScheduler(provider).parse("every monday at 9am", tenant_id=TENANT)
    _assert_registry_model(llm_server)


async def test_nl_trigger(provider: Any, llm_server: LocalLLMServer) -> None:
    from app.workflow.nl_trigger import NLTriggerParseError, NLTriggerResolver

    with pytest.raises(NLTriggerParseError):  # the server's reply is not a trigger
        await NLTriggerResolver(llm_provider=provider)._llm_parse(
            "when a refund over 500 dollars is filed", tenant_ctx=SimpleNamespace(tenant_id="")
        )
    _assert_registry_model(llm_server)


async def test_org_strategic_brief_no_longer_always_claude_sonnet(
    provider: Any, llm_server: LocalLLMServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.org import advanced_services

    monkeypatch.setattr(advanced_services, "_brief_provider", lambda: provider)
    await advanced_services.StrategicAdvisor()._llm_brief(
        "org-1", "Acme", {"health": "green"}, [], tenant_id=TENANT
    )
    _assert_registry_model(llm_server)


async def test_org_team_formation(provider: Any, llm_server: LocalLLMServer) -> None:
    from app.org.team_formation import TeamFormationEngine

    await TeamFormationEngine(llm_provider=provider)._extract_capabilities(
        "launch a marketing campaign", tenant_id=TENANT
    )
    _assert_registry_model(llm_server)


async def test_org_collaboration_tick(provider: Any, llm_server: LocalLLMServer) -> None:
    from app.org.brain_collaboration import LLMProviderCollaborationGateway
    from app.org.model_gateway import ModelGateway

    gateway = LLMProviderCollaborationGateway(provider, gateway=ModelGateway())
    await gateway.complete_short("say hi", max_tokens=20, tenant_id=TENANT)
    _assert_registry_model(llm_server)


async def test_guardrail_judge(provider: Any, llm_server: LocalLLMServer) -> None:
    from app.intelligence.guardrail_engine import LLMJudge

    async def _factory() -> Any:
        return provider

    await LLMJudge(_factory).evaluate("please summarise this quarterly report for me",
                                      tenant_id=TENANT)
    _assert_registry_model(llm_server)


async def test_tool_self_heal(provider: Any, llm_server: LocalLLMServer) -> None:
    from app.mcp.tool_intelligence import SelfHealingToolCaller

    with pytest.raises(ValueError):  # the server's reply is not JSON arguments
        await SelfHealingToolCaller(provider)._llm_fix_arguments(
            "search", {"type": "object", "properties": {"q": {"type": "string"}}},
            {"query": "x"}, "missing q", tenant_ctx=SimpleNamespace(tenant_id=TENANT),
        )
    _assert_registry_model(llm_server)


@pytest.mark.parametrize("role", ["rag_hyde", "rag_raptor", "rag_synthesis",
                                  "rag_citation_verify", "kg_entity_extraction",
                                  "memory_consolidation", "chat_summary", "skill"])
async def test_complete_decision_roles_sending_no_model(
    provider: Any, llm_server: LocalLLMServer, role: str
) -> None:
    from app.providers.base import CompletionRequest, Message
    from app.providers.guarded_completion import complete_decision

    await complete_decision(
        provider, CompletionRequest(messages=[Message(role="user", content="x")], model=""),
        role=role, tenant_id=TENANT,
    )
    _assert_registry_model(llm_server)


async def test_rag_strategy_resolvers_pick_the_registry_model(provider: Any) -> None:
    from app.ai_router.role_preference import rag_role_for_strategy, servable_role_model
    from app.rag.contracts import RAGStrategy

    for strategy in (RAGStrategy.HYDE, RAGStrategy.CORRECTIVE, RAGStrategy.MULTI_HOP):
        assert servable_role_model(rag_role_for_strategy(strategy), provider) == MODEL


async def test_knowledge_indexing_llm_resolves_the_registry_model(provider: Any) -> None:
    from app.api.knowledge import _resolve_indexing_llm

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        retrieval_gateway=None, _app_provider=provider)))
    deps = await _resolve_indexing_llm(request, SimpleNamespace(tenant_id=TENANT), {"raptor"})
    assert [d.model for d in deps.values()] == [MODEL]


async def test_chat_model_list_and_preferred_model(provider: Any) -> None:
    from app.chat.intent import IntentRouter
    from app.chat.service import _chat_model

    assert IntentRouter().available_models() == [MODEL]
    assert _chat_model(provider, "")[0] == MODEL
    assert _chat_model(provider, MODEL)[0] == MODEL
    # A stale / invented preferred model is ignored, never sent to the provider.
    assert _chat_model(provider, "claude-3-5-sonnet")[0] == MODEL


async def test_chat_streamed_answer_runs_on_the_registry_model(
    provider: Any, llm_server: LocalLLMServer
) -> None:
    from app.chat.service import _chat_model
    from app.providers.base import CompletionRequest, Message

    model, _fallbacks = _chat_model(provider, "")
    request = CompletionRequest(messages=[Message(role="user", content="hi")], model=model)
    chunks = [c async for c in provider.stream_complete(request)]
    assert "".join(chunks)
    _assert_registry_model(llm_server)


def test_self_optimizer_recommends_the_cheapest_registry_model(provider: Any) -> None:
    from app.intelligence.self_optimizer_v2 import SelfOptimizerV2

    actions = SelfOptimizerV2.__new__(SelfOptimizerV2).plan_improvement_actions(
        tenant_id=TENANT, goal_id="g1", scores={"cost_efficiency": 0.1, "latency": 0.1},
    )
    routing = [a for a in actions if a.action_type == "update_model_routing"]
    assert [a.payload["model"] for a in routing] == [MODEL]


async def test_models_active_endpoint_reports_the_registry_model(provider: Any) -> None:
    from app.api.model_registry import get_active_model

    request = SimpleNamespace(state=SimpleNamespace(
        tenant=SimpleNamespace(tenant_id=TENANT, plan=SimpleNamespace(value="enterprise"))))
    out = await get_active_model(request)
    assert out["model_id"] == MODEL
    assert out["configured"] is True
    assert out["source"] == "registry_cheapest"


def test_isolated_worker_envelope_carries_the_registry_model(provider: Any) -> None:
    from app.services.goal_service import isolated_role_models

    assert isolated_role_models() == {
        "planning": MODEL, "execution": MODEL, "verification": MODEL,
    }
    assert isolated_role_models(byok_model="tenant-model")["planning"] == "tenant-model"
