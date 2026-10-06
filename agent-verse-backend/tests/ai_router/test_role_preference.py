"""The saved reasoning order picks each role's model, over the deployment role map.

Every NVIDIA / on-prem deployment pins roles automatically (planning on NVIDIA,
execution and verification on Qwen). The operator's saved reasoning order used
to decide only the failover chain; now it wins in both role routers.
"""

_ISOLATE_PROVIDER_ENV = True

import pytest

from app.agent.model_router import ModelRouter
from app.ai_router.model_orchestrator import ModelOrchestratorAdapter
from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.ai_router.role_preference import preferred_role_model

_TG, _TU = ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_USE
NEMOTRON, QWEN = "nvidia/nemotron-3-super-120b-a12b", "Qwen/Qwen3.5-4B"
ROLE_MAP = {"planning": NEMOTRON, "execution": QWEN, "verification": QWEN}


def _add(provider, model_id, *, tools=True, source="override", cost=0.0):
    caps = [_TG, _TU] if tools else [_TG]
    model_registry.register_configured(
        ModelEndpoint(provider=provider, model_id=model_id, display_name=model_id,
                      capabilities=caps, supports_tools=tools, cost_per_1k_input=cost,
                      extra={"source": source})
    )


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    model_registry.clear_configured()
    model_registry.set_preferences({})
    _add("nvidia", NEMOTRON, source="env")
    _add("onprem", QWEN, source="env")
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


def _routers():
    out = []
    for router in (ModelRouter("openai"), ModelOrchestratorAdapter()):
        router.set_role_map(ROLE_MAP)
        out.append(router)
    return out


def test_without_a_saved_order_the_role_map_decides():
    for router in _routers():
        assert router.model_for("planning") == NEMOTRON
        assert router.model_for("execution") == QWEN


def test_the_saved_order_wins_over_the_role_map_for_every_role():
    _add("custom", "qwen-by-hand")
    model_registry.set_preferences({"text_generation": ["custom/qwen-by-hand"]})
    for router in _routers():
        for role in ("planning", "execution", "verification"):
            assert router.model_for(role) == "qwen-by-hand", (type(router).__name__, role)


def test_a_ranked_env_model_wins_too():
    model_registry.set_preferences({"text_generation": [f"onprem/{QWEN}"]})
    for router in _routers():
        assert router.model_for("planning") == QWEN  # role map said NEMOTRON


def test_execution_skips_a_ranked_model_without_tool_use():
    _add("custom", "chat-only", tools=False)
    model_registry.set_preferences({"text_generation": ["custom/chat-only"]})
    for router in _routers():
        assert router.model_for("planning") == "chat-only"
        assert router.model_for("execution") == QWEN  # tools required: role map


def test_a_ranked_model_without_provider_credentials_is_skipped(monkeypatch):
    _add("groq", "llama-3.3-70b-versatile")
    model_registry.set_preferences({"text_generation": ["groq/llama-3.3-70b-versatile"]})
    for router in _routers():
        assert router.model_for("planning") == NEMOTRON
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")
    for router in _routers():
        assert router.model_for("planning") == "llama-3.3-70b-versatile"


def test_the_saved_order_beats_env_pins_but_not_a_per_agent_override(monkeypatch):
    monkeypatch.setenv("DEFAULT_PLANNING_MODEL", "pinned-by-env")
    _add("custom", "ranked")
    model_registry.set_preferences({"text_generation": ["custom/ranked"]})
    router = ModelRouter("openai")
    assert router.model_for("planning") == "ranked"
    assert router.with_override("agent-pinned").model_for("planning") == "agent-pinned"


def test_non_reasoning_roles_are_untouched():
    _add("custom", "ranked")
    model_registry.set_preferences({"text_generation": ["custom/ranked"]})
    assert preferred_role_model("embedding") == ""
    assert preferred_role_model("planning") == "ranked"


def test_a_tenant_routing_policy_pin_outranks_the_deployment_wide_order():
    _add("custom", "ranked")
    model_registry.set_preferences({"text_generation": ["custom/ranked"]})
    for router in _routers():
        router.set_role_map({**ROLE_MAP, "planning": "tenant-pinned"})
        router.set_policy_roles({"planning": "tenant-pinned"})
        assert router.model_for("planning") == "tenant-pinned"
        assert router.model_for("execution") == "ranked"  # not pinned by the tenant


# ── single reasoning calls outside the graph roles (synthesis, eval judges) ──


def test_single_calls_keep_the_provider_default_without_a_saved_order():
    from app.ai_router.role_preference import preferred_model_and_fallbacks

    class _P:
        _default_model = "provider-default"

    assert preferred_model_and_fallbacks("planning", _P()) == ("", [])
    assert preferred_model_and_fallbacks("judge", _P()) == ("", [])


def test_single_calls_follow_the_saved_order_with_the_default_last():
    from app.ai_router.role_preference import preferred_model_and_fallbacks

    class _P:
        _default_model = "provider-default"

    _add("custom", "ranked-1")
    _add("custom", "ranked-2")
    model_registry.set_preferences({"text_generation": ["custom/ranked-1", "custom/ranked-2"]})
    model, fallbacks = preferred_model_and_fallbacks("planning", _P())
    assert model == "ranked-1"
    assert fallbacks[0] == "ranked-2"
    assert fallbacks[-1] == "provider-default"


@pytest.mark.asyncio
async def test_answer_synthesis_and_eval_judges_use_the_saved_order(monkeypatch):
    from app.agent.synthesis import AnswerSynthesizer
    from app.intelligence.eval_runner import EvalRunner

    _add("custom", "ranked-1")
    model_registry.set_preferences({"text_generation": ["custom/ranked-1"]})
    seen: list[tuple[str, str, list[str]]] = []

    class _Resp:
        content = "0.9 [Step 1]"

    async def _fake_complete_decision(provider, req, *, role, fallback_models=(), **_k):
        seen.append((role, req.model, list(fallback_models)))
        return _Resp()

    monkeypatch.setattr(
        "app.providers.guarded_completion.complete_decision", _fake_complete_decision
    )

    class _P:
        _default_model = "provider-default"

    await AnswerSynthesizer(llm_provider=_P())._synthesize_with_llm(
        "goal", [{"step_index": 0, "tool_name": "", "output_excerpt": "391"}], []
    )
    await EvalRunner()._llm_rate("rate it", _P(), role="eval_accuracy", tenant_ctx=None,
                                 goal_id=None)
    by_role = {role: (model, fb) for role, model, fb in seen}
    for role in ("answer_synthesis", "eval_accuracy"):
        model, fallbacks = by_role[role]
        assert model == "ranked-1"
        assert fallbacks[-1] == "provider-default"  # the provider's own model is last resort
