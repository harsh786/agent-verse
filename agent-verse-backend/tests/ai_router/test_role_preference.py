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
