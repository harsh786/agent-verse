"""resolve_reasoning — the ONE reasoning resolver — and the central hook.

Order: per-agent/goal override (BYOK keeps its own model) > tenant policy pin >
registry text-generation order (role-eligible; execution needs tools) >
DEFAULT_<ROLE>_MODEL > deployment role map > NVIDIA_MODEL/DEFAULT_MODEL/
OPENAI_MODEL > registry head > provider default (empty registry only) >
ModelNotConfiguredError. Every request sent with model ``""`` / ``"default"``
through ``complete_decision`` or a ModelDispatchProvider gets that model.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.ai_router.resolve import (
    ModelNotConfiguredError,
    cheapest_reasoning_model,
    is_unset_model,
    llm_role_scope,
    registry_text_model_ids,
    resolve_reasoning,
    validated_preference,
)
from app.providers.base import CompletionRequest, CompletionResponse, Message

_TG, _TU = ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_USE
_ENV = (
    "NVIDIA_API_KEY", "NVIDIA_MODEL", "OPENAI_MODEL", "DEFAULT_MODEL", "OPENAI_BASE_URL",
    "DEFAULT_PLANNING_MODEL", "DEFAULT_EXECUTION_MODEL", "DEFAULT_VERIFICATION_MODEL",
)


def _add(model_id: str, *, tools: bool = True, cost: float = 0.0,
         source: str = "env", provider: str = "onprem") -> None:
    model_registry.register_configured(ModelEndpoint(
        provider=provider, model_id=model_id, display_name=model_id,
        capabilities=[_TG, _TU] if tools else [_TG], supports_tools=tools,
        cost_per_1k_input=cost, extra={"source": source},
        # an operator-added model names its own endpoint (eligible without env keys)
        base_url="http://127.0.0.1:9/v1" if source == "override" else None,
    ))


def _rank(*model_ids: str, provider: str = "onprem") -> None:
    model_registry.set_preferences({"text_generation": [f"{provider}/{m}" for m in model_ids]})


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.setattr(
        "app.ai_router.deployment_roles.deployment_role_models", lambda *a, **k: {}
    )
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


class _Router:
    def __init__(self, *, override: str = "", policy: dict[str, str] | None = None,
                 role_map: dict[str, str] | None = None) -> None:
        self._override = override
        self._policy_roles = policy or {}
        self._role_map = role_map or {}


class _Provider:
    def __init__(self, default: str = "provider-default", *, byok: str | None = None) -> None:
        self._default_model = default
        self.models: list[str] = []
        if byok:
            self._byok_tenant_id = byok

    async def complete(self, request: Any) -> CompletionResponse:
        self.models.append(request.model)
        return CompletionResponse(content="ok", model=request.model or self._default_model,
                                  input_tokens=1, output_tokens=1)


# ── the order ────────────────────────────────────────────────────────────────


def test_each_tier_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = _Provider()
    # 8. empty registry: the provider default
    res = resolve_reasoning("planning", provider=provider)
    assert (res.model, res.source) == ("provider-default", "provider_default")
    # 7. registry head (registry-only deployment), cheapest first
    _add("pricey", cost=0.01, source="override")
    _add("cheap", cost=0.001, source="override")
    res = resolve_reasoning("planning", provider=provider)
    assert (res.model, res.source) == ("cheap", "registry_cheapest")
    assert res.fallbacks == ("pricey", "provider-default")
    # 6. env default
    monkeypatch.setenv("DEFAULT_MODEL", "env-default")
    assert resolve_reasoning("planning").model == "env-default"
    # 5. deployment role map (the goal router's)
    assert resolve_reasoning("planning", router=_Router(role_map={"planning": "mapped"})).model \
        == "mapped"
    # 4. per-role env pin
    monkeypatch.setenv("DEFAULT_PLANNING_MODEL", "plan-pin")
    res = resolve_reasoning("planning", router=_Router(role_map={"planning": "mapped"}))
    assert (res.model, res.source) == ("plan-pin", "env_pin")
    # 3. saved registry order
    _rank("pricey")
    res = resolve_reasoning("planning")
    assert (res.model, res.source) == ("pricey", "registry_preference")
    # 2. tenant policy pin
    res = resolve_reasoning("planning", router=_Router(policy={"planning": "tenant-pin"}))
    assert (res.model, res.source) == ("tenant-pin", "tenant_pin")
    # 1. override (explicit or the router's with_override)
    assert resolve_reasoning("planning", override="agent-pin",
                             router=_Router(policy={"planning": "tenant-pin"})).model \
        == "agent-pin"
    assert resolve_reasoning("planning", router=_Router(override="goal-pin")).model == "goal-pin"


def test_nothing_configured_raises_an_honest_error() -> None:
    with pytest.raises(ModelNotConfiguredError) as exc:
        resolve_reasoning("chat_qa")
    assert "Model Registry" in str(exc.value)
    assert exc.value.capability == "reasoning"


def test_registry_with_no_eligible_model_does_not_fall_to_the_provider_default() -> None:
    _add("chat-only", tools=False, source="override")
    # execution needs tool use: the chat-only model is not eligible, and the
    # registry is not empty, so the provider default is NOT silently used.
    with pytest.raises(ModelNotConfiguredError):
        resolve_reasoning("executor", provider=_Provider())
    assert resolve_reasoning("planner", provider=_Provider()).model == "chat-only"


def test_execution_skips_a_ranked_model_without_tools() -> None:
    _add("no-tools", tools=False)
    _add("tooled")
    _rank("no-tools", "tooled")
    assert resolve_reasoning("planning").model == "no-tools"
    assert resolve_reasoning("execution").model == "tooled"
    assert resolve_reasoning("workflow_step").model == "tooled"


def test_byok_provider_keeps_its_own_model(monkeypatch: pytest.MonkeyPatch) -> None:
    _add("registry-model")
    _rank("registry-model")
    monkeypatch.setenv("DEFAULT_MODEL", "env-default")
    byok = _Provider("tenant-model", byok="t1")
    res = resolve_reasoning("planning", provider=byok)
    assert (res.model, res.source) == ("tenant-model", "tenant_pin")
    assert resolve_reasoning("planning", provider=byok, override="explicit").model == "explicit"


def test_router_bound_provider_is_used() -> None:
    router = _Router()
    router._bound_provider = _Provider("bound-default")  # type: ignore[attr-defined]
    assert resolve_reasoning("planning", router=router).model == "bound-default"


def test_role_table_covers_the_new_roles() -> None:
    from app.ai_router.role_preference import role_task_type

    expected = {
        "refine": "execution", "execute": "execution", "judge": "judge",
        "summarization": "classification", "extraction": "classification",
        "chat": "planning", "critique": "verification", "synthesis": "planning",
        "rag_hyde": "classification", "rag_raptor": "classification",
        "rag_synthesis": "planning", "rag_citation_verify": "verification",
        "org_quality_gate": "judge", "org_strategic_brief": "planning",
        "chat_qa": "planning", "chat_summary": "classification",
        "nl_scheduler": "classification", "nl_trigger": "classification",
        "tool_self_heal": "classification", "kg_entity_extraction": "classification",
        "workflow_llm_step": "planning", "skill": "planning",
    }
    assert {r: role_task_type(r) for r in expected} == expected


def test_both_routers_route_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent.model_router import ModelRouter
    from app.ai_router.model_orchestrator import ModelOrchestratorAdapter

    _add("judge-model")
    _rank("judge-model")
    for router in (ModelRouter(), ModelOrchestratorAdapter()):
        assert router.model_for("judge") == "judge-model", type(router).__name__


# ── helpers ──────────────────────────────────────────────────────────────────


def test_helpers() -> None:
    assert is_unset_model("") and is_unset_model("default") and is_unset_model(None)
    assert not is_unset_model("m")
    _add("b-model", cost=0.002, source="override")
    _add("a-model", cost=0.001, source="override")
    assert registry_text_model_ids("chat") == ["a-model", "b-model"]
    assert cheapest_reasoning_model("execution") == "a-model"
    assert validated_preference("b-model", "chat_qa") == "b-model"
    assert validated_preference("gpt-4o", "chat_qa") == ""  # not configured
    assert validated_preference("default") == ""


# ── the central hook ─────────────────────────────────────────────────────────


def _req(model: str = "") -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content="x")], model=model)


@pytest.mark.parametrize("unset", ["", "default"])
async def test_complete_decision_fills_an_unset_model_from_the_registry(unset: str) -> None:
    from app.providers.guarded_completion import complete_decision
    from app.providers.model_dispatch import ModelDispatchProvider

    _add("registry-head", cost=0.001)
    _add("registry-next", cost=0.002)
    inner = _Provider()
    await complete_decision(ModelDispatchProvider(inner), _req(unset), role="nl_scheduler",
                            charge=False)
    assert inner.models == ["registry-head"]


async def test_complete_decision_fails_over_along_the_registry_order() -> None:
    from app.providers.guarded_completion import complete_decision
    from app.providers.model_dispatch import ModelDispatchProvider

    _add("registry-head", cost=0.001)
    _add("registry-next", cost=0.002)

    class _Flaky(_Provider):
        async def complete(self, request: Any) -> CompletionResponse:
            self.models.append(request.model)
            if request.model == "registry-head":
                raise RuntimeError("down")
            return CompletionResponse(content="ok", model=request.model)

    inner = _Flaky()
    resp = await complete_decision(ModelDispatchProvider(inner), _req(), role="org_collaboration",
                                   charge=False)
    assert resp.model == "registry-next"
    assert inner.models[0] == "registry-head"


async def test_complete_decision_leaves_byok_and_fake_alone() -> None:
    from app.providers.fake import FakeProvider
    from app.providers.guarded_completion import complete_decision

    _add("registry-head")
    byok = _Provider("tenant-model", byok="t1")
    await complete_decision(byok, _req(), role="chat_qa", charge=False)
    assert byok.models == [""]

    seen: list[str] = []

    class _Fake(FakeProvider):
        async def complete(self, request: Any) -> Any:
            seen.append(request.model)
            return await super().complete(request)

    await complete_decision(_Fake(responses=["ok"]), _req(), role="chat_qa", charge=False)
    assert seen == [""]


async def test_complete_decision_never_hands_a_plain_provider_a_model_it_cannot_serve() -> None:
    from app.providers.guarded_completion import complete_decision

    _add("registry-head")
    plain = _Provider("own-model")
    plain.__dict__["_default_model"] = "own-model"
    await complete_decision(plain, _req(), role="agent_router", charge=False)
    assert plain.models == ["own-model"]  # its own model (servable), never registry-head


async def test_nothing_configured_leaves_the_request_for_the_providers_honest_error() -> None:
    from app.providers.guarded_completion import complete_decision
    from app.providers.model_dispatch import ModelDispatchProvider

    class _NoDefault(_Provider):
        def __init__(self) -> None:
            super().__init__("")

    inner = _NoDefault()
    await complete_decision(ModelDispatchProvider(inner), _req("default"), role="chat",
                            charge=False)
    assert inner.models == [""]  # "default" is never sent as a model id


async def test_dispatch_provider_fills_direct_calls_by_role_scope() -> None:
    from app.providers.model_dispatch import ModelDispatchProvider

    _add("no-tools", tools=False)
    _add("tooled")
    _rank("no-tools", "tooled")
    inner = _Provider()
    dispatch = ModelDispatchProvider(inner)
    await dispatch.complete(_req())
    with llm_role_scope("execution"):
        await dispatch.complete(_req())
    await dispatch.complete(_req("explicit"))
    assert inner.models == ["no-tools", "tooled", "explicit"]


async def test_dispatch_provider_fails_over_on_a_direct_call() -> None:
    from app.providers.model_dispatch import ModelDispatchProvider

    _add("head", cost=0.001)
    _add("next", cost=0.002)

    class _Flaky(_Provider):
        async def complete(self, request: Any) -> CompletionResponse:
            self.models.append(request.model)
            if request.model == "head":
                raise RuntimeError("down")
            return CompletionResponse(content="ok", model=request.model)

    inner = _Flaky()
    resp = await ModelDispatchProvider(inner).complete(_req())
    assert resp.model == "next"
    assert inner.models == ["head", "next"]


async def test_dispatch_provider_leaves_a_byok_inner_alone() -> None:
    from app.providers.model_dispatch import ModelDispatchProvider

    _add("registry-head")
    byok = _Provider("tenant-model", byok="t1")
    await ModelDispatchProvider(byok).complete(_req())
    assert byok.models == [""]
