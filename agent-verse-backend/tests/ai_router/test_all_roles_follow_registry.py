"""Every LLM role follows the operator's Model Registry ranking — not only the big three.

Live finding (tests/real_world/test_onprem_model_registry_e2e.py): the operator
ranked the on-prem ``Qwen/Qwen3.5-4B`` first for text generation and pinned
planning/execution/verification to it. Every role's ``role_calls`` showed Qwen
EXCEPT ``supervisor``, which ran on the env-default cloud model
(``nvidia/nemotron-3-super-120b-a12b``): ``SupervisorAgent`` sent
``planner._default_model``. The same bypass existed for every pattern that asked
for "the provider default" (``model=""``): debate, self-consistency,
tree-of-thoughts, peer review, the agent router, the goal classifier, judges,
guardrails, memory consolidation and the RAG strategy LLMs.

The single resolver is ``app.ai_router.role_preference.resolve_role_model`` and
``ROLE_TASK_TYPES`` enumerates the roles. These tests pin: with a saved order
``[onprem/Qwen]`` and a cloud env default, EVERY known role resolves to Qwen —
through the resolver, through both role routers, through the graph's pattern
provider (``ChargingProvider``), through the supervisor, and through
``complete_decision`` for calls that send no model.
"""

_ISOLATE_PROVIDER_ENV = True

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.agent.model_router import ModelRouter
from app.ai_router.model_orchestrator import ModelOrchestratorAdapter
from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.ai_router.role_preference import (
    REASONING_ROLES,
    ROLE_TASK_TYPES,
    ROUTED_TASK_TYPES,
    resolve_role_model,
    role_task_type,
)

_TG, _TU = ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_USE
CLOUD, QWEN = "nvidia/nemotron-3-super-120b-a12b", "Qwen/Qwen3.5-4B"
# What an NVIDIA + on-prem deployment's automatic role map looks like.
ROLE_MAP = {"planning": CLOUD, "execution": CLOUD, "verification": CLOUD}


def _add(provider: str, model_id: str, *, source: str = "env") -> None:
    model_registry.register_configured(
        ModelEndpoint(provider=provider, model_id=model_id, display_name=model_id,
                      capabilities=[_TG, _TU], supports_tools=True, cost_per_1k_input=0.0,
                      extra={"source": source})
    )


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> Any:
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    model_registry.clear_configured()
    model_registry.set_preferences({})
    _add("nvidia", CLOUD)
    _add("onprem", QWEN)
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


def _rank_qwen_first() -> None:
    model_registry.set_preferences({"text_generation": [f"onprem/{QWEN}"]})


class _CloudDefaultProvider:
    """A provider whose own (env) default is the cloud model."""

    _default_model = CLOUD


def _routers() -> list[Any]:
    out: list[Any] = []
    for router in (ModelRouter("nvidia"), ModelOrchestratorAdapter()):
        router.set_role_map(ROLE_MAP)
        out.append(router)
    return out


# ── the role table itself ───────────────────────────────────────────────────


def test_every_known_role_maps_to_a_reasoning_task_type() -> None:
    assert "supervisor" in ROLE_TASK_TYPES
    for role, task in ROLE_TASK_TYPES.items():
        assert task in REASONING_ROLES, (role, task)


def test_role_families_built_at_runtime_are_known() -> None:
    assert role_task_type("coordination_moa_aggregate") == "planning"
    assert role_task_type("rag_whatever_new") == "classification"
    assert role_task_type("eval_new_dimension") == "judge"
    assert role_task_type("chat_qa") == ""  # out of scope: untouched


# ── with Qwen ranked first, every role resolves to Qwen ─────────────────────


def test_every_role_resolves_to_the_ranked_model_without_a_router(monkeypatch) -> None:
    monkeypatch.setenv("NVIDIA_MODEL", CLOUD)
    monkeypatch.setenv("DEFAULT_PLANNING_MODEL", CLOUD)  # env pins rank below the order
    _rank_qwen_first()
    provider = _CloudDefaultProvider()
    wrong = {r: m for r in ROLE_TASK_TYPES
             if (m := resolve_role_model(r, provider=provider)) != QWEN}
    assert not wrong, wrong


def test_every_role_resolves_to_the_ranked_model_through_both_routers(monkeypatch) -> None:
    monkeypatch.setenv("NVIDIA_MODEL", CLOUD)
    _rank_qwen_first()
    provider = _CloudDefaultProvider()
    for router in _routers():
        wrong = {r: m for r in ROLE_TASK_TYPES
                 if (m := resolve_role_model(r, router=router, provider=provider)) != QWEN}
        assert not wrong, (type(router).__name__, wrong)


def test_supervisor_is_a_routed_task_type_on_both_routers(monkeypatch) -> None:
    monkeypatch.setenv("NVIDIA_MODEL", CLOUD)
    assert "supervisor" in ROUTED_TASK_TYPES
    _rank_qwen_first()
    for router in _routers():
        assert router.model_for("supervisor") == QWEN, type(router).__name__


def test_supervisor_follows_the_tenant_planning_pin(monkeypatch) -> None:
    """The operator pinned planning (the E2E pins planning/execution/verification):
    the supervisor's decomposition is planning work and follows that pin."""
    monkeypatch.setenv("NVIDIA_MODEL", CLOUD)
    for router in _routers():
        router.set_policy_roles({"planning": QWEN})
        assert resolve_role_model("supervisor", router=router,
                                  provider=_CloudDefaultProvider()) == QWEN


def test_without_an_order_or_pin_a_role_keeps_the_env_pin_then_provider_default(
    monkeypatch,
) -> None:
    provider = _CloudDefaultProvider()
    assert resolve_role_model("agent_router", provider=provider) == CLOUD
    monkeypatch.setenv("DEFAULT_PLANNING_MODEL", "pinned-planner")
    assert resolve_role_model("supervisor", provider=provider) == "pinned-planner"
    assert resolve_role_model("debate", provider=provider) == "pinned-planner"


def test_a_per_agent_override_still_wins(monkeypatch) -> None:
    _rank_qwen_first()
    router = ModelRouter("nvidia").with_override("agent-pinned")
    assert resolve_role_model("supervisor", router=router) == "agent-pinned"


# ── the call paths that used to bypass the resolution ───────────────────────


@dataclass
class _Resp:
    content: str = '{"sub_tasks": [{"goal": "only task"}]}'
    model: str = ""
    input_tokens: int = 1
    output_tokens: int = 1
    total_tokens: int = 2
    tool_calls: list[Any] = field(default_factory=list)


class _RecordingProvider:
    _default_model = CLOUD

    def __init__(self) -> None:
        self.models: list[str] = []

    async def complete(self, request: Any) -> _Resp:
        self.models.append(request.model)
        return _Resp(model=request.model)


@pytest.mark.asyncio
async def test_supervisor_decomposes_and_synthesizes_on_the_ranked_model(monkeypatch) -> None:
    from app.agent.supervisor import SubAgentTask, SupervisorAgent

    monkeypatch.setenv("NVIDIA_MODEL", CLOUD)
    _rank_qwen_first()
    seen: list[tuple[str, str]] = []

    async def _fake_complete_decision(provider, req, *, role, **_k):
        seen.append((role, req.model))
        return _Resp()

    monkeypatch.setattr(
        "app.providers.guarded_completion.complete_decision", _fake_complete_decision
    )
    sup = SupervisorAgent(planner_provider=_RecordingProvider(), goal_service=object())
    await sup._decompose("goal", tenant_ctx=None)
    done = SubAgentTask(goal="t")
    done.result = "r"
    await sup._synthesize("goal", [done], [], tenant_ctx=None)
    assert seen == [("supervisor", QWEN), ("supervisor", QWEN)]


@pytest.mark.asyncio
async def test_graph_pattern_provider_serves_the_role_model(monkeypatch) -> None:
    """ChargingProvider (supervisor / debate / ToT / self-consistency / peer review
    inside the graph): a request for the provider default goes to the role model."""
    from app.agent.nodes.llm_cost import ChargingProvider
    from app.providers.base import CompletionRequest, Message

    charged: list[str] = []

    async def _charge(graph, *, resp, role, model, agent_state, tenant_ctx, **_k):
        charged.append(model)
        return 0.0

    monkeypatch.setattr("app.agent.nodes.llm_cost.charge_llm_call", _charge)
    inner = _RecordingProvider()
    proxy = ChargingProvider(inner, graph=None, role="supervisor", agent_state=None,
                             tenant_ctx=None, model=QWEN)
    assert proxy._default_model == QWEN  # what pattern code reads as "the model"
    for asked in ("", CLOUD):
        await proxy.complete(CompletionRequest(messages=[Message(role="user", content="x")],
                                               model=asked))
    await proxy.complete(CompletionRequest(messages=[Message(role="user", content="x")],
                                           model="explicit-model"))
    assert inner.models == [QWEN, QWEN, "explicit-model"]
    assert charged == [QWEN, QWEN, "explicit-model"]


@pytest.mark.asyncio
async def test_complete_decision_routes_a_known_role_that_sends_no_model(monkeypatch) -> None:
    """agent_router / goal_classifier / judges / guardrails / memory / RAG send
    ``model=""``: on the deployment dispatcher they now run on the ranked model."""
    from app.providers.base import CompletionRequest, Message
    from app.providers.guarded_completion import complete_decision
    from app.providers.model_dispatch import ModelDispatchProvider

    monkeypatch.setenv("NVIDIA_MODEL", CLOUD)
    _rank_qwen_first()
    inner = _RecordingProvider()
    dispatch = ModelDispatchProvider(inner)
    for role in ("agent_router", "goal_classifier", "memory_consolidation", "rag_strategy",
                 "guardrail_toxicity", "consensus_judge", "debate_vote", "chat_qa"):
        await complete_decision(
            dispatch, CompletionRequest(messages=[Message(role="user", content="x")], model=""),
            role=role, charge=False,
        )
    # chat_qa is not a known agent role: left on the provider default ("").
    assert inner.models == [QWEN] * 7 + [""]


@pytest.mark.asyncio
async def test_complete_decision_never_sends_a_model_the_provider_cannot_serve(
    monkeypatch,
) -> None:
    """A single-vendor provider (tenant BYO key) is not handed the on-prem model."""
    from app.providers.base import CompletionRequest, Message
    from app.providers.guarded_completion import complete_decision

    _rank_qwen_first()
    plain = _RecordingProvider()
    await complete_decision(
        plain, CompletionRequest(messages=[Message(role="user", content="x")], model=""),
        role="agent_router", charge=False,
    )
    assert plain.models == [""]


def test_refine_resolves_like_every_other_role(monkeypatch) -> None:
    """``model_for("refine")`` is not a task type: it fell through to the env
    single-model fallback (the cloud model)."""
    monkeypatch.setenv("NVIDIA_API_KEY", "test-nvidia-key")
    monkeypatch.setenv("NVIDIA_MODEL", CLOUD)
    router = ModelRouter("nvidia")
    assert router.model_for("refine") == CLOUD  # the old call: the bypass
    _rank_qwen_first()
    assert resolve_role_model("refine", router=router) == QWEN


def test_rag_retrieval_llm_resolution_follows_the_order_on_the_dispatcher(monkeypatch) -> None:
    """The RAG strategy LLM (query rewrite / HyDE / verifier) used ResolvedLLM.model =
    the provider default; on the deployment dispatcher it now follows the order, and
    a tenant's single-vendor provider keeps its own default."""
    from app.ai_router.role_preference import servable_role_model
    from app.providers.model_dispatch import ModelDispatchProvider

    monkeypatch.setenv("NVIDIA_MODEL", CLOUD)
    _rank_qwen_first()
    assert servable_role_model("rag_strategy", ModelDispatchProvider(_RecordingProvider())) == QWEN
    assert servable_role_model("rag_strategy", _RecordingProvider()) == CLOUD
