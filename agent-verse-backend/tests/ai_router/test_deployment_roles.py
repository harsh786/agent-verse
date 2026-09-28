"""Per-role model selection follows the deployment's configuration — per goal,
restricted to what the goal's provider can serve.

Measured against the real hybrid cluster (on-prem Qwen/Gemma + NVIDIA kimi-k3):
every executor call went to the hosted model (~100 s each) because the
configured-model registry held only NVIDIA_MODEL and ranked unpriced models by
quality. Review of the first fix found the map must also be provider-aware (a
tenant on Anthropic, or a single-endpoint worker, must never get on-prem ids),
must ignore env values main.py synthesised, and must fail closed on an unknown
small-model context window.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.ai_router import deployment_roles as dr

QWEN, GEMMA, KIMI = "Qwen/Qwen3.5-4B", "google/gemma-4-E2B", "moonshotai/kimi-k3"


def _s(**kw: object) -> SimpleNamespace:
    base = dict(
        onprem_enabled=False, onprem_qwen_base_url="", onprem_qwen_model=QWEN,
        onprem_gemma_base_url="", onprem_gemma_model=GEMMA,
        nvidia_api_key="", nvidia_model=KIMI,
        default_planning_model="", default_execution_model="", default_verification_model="",
    )
    base.update(kw)
    return SimpleNamespace(**base)


HYBRID = dict(onprem_enabled=True, onprem_qwen_base_url="http://q/v1",
              onprem_gemma_base_url="http://g/v1", nvidia_api_key="k")


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("DEFAULT_PLANNING_MODEL", "DEFAULT_EXECUTION_MODEL", "DEFAULT_VERIFICATION_MODEL"):
        monkeypatch.delenv(var, raising=False)
    dr._CONTEXT_WINDOWS.clear()


def test_hybrid_plans_on_nvidia_and_executes_locally() -> None:
    roles = dr.deployment_role_models(_s(**HYBRID))
    assert roles == {"planning": KIMI, "execution": QWEN, "verification": QWEN}
    assert dr.role_fallback_chain(roles) == [QWEN, KIMI]  # local before hosted


def test_role_map_is_restricted_to_servable_models() -> None:
    # A worker whose provider only reaches NVIDIA must not be handed the Qwen id.
    roles = dr.deployment_role_models(_s(**HYBRID), servable={KIMI})
    assert roles == {"planning": KIMI}


def test_servable_models_only_for_multi_endpoint_dispatchers() -> None:
    assert dr.servable_models(SimpleNamespace(_endpoints={KIMI: 1, QWEN: 2})) == {KIMI, QWEN}
    # Tenant-configured Anthropic/OpenAI provider: no role map at all.
    assert dr.servable_models(SimpleNamespace(_default_model="claude")) is None


def test_small_model_verifies_only_when_its_window_is_known_to_fit() -> None:
    s = _s(onprem_enabled=True, onprem_qwen_base_url="http://q/v1",
           onprem_gemma_base_url="http://g/v1")
    assert dr.deployment_role_models(s)["verification"] == QWEN  # unknown -> fail closed
    dr.record_context_window(GEMMA, 1024)  # what the real cluster reports
    assert dr.deployment_role_models(s)["verification"] == QWEN
    dr.record_context_window(GEMMA, 8192)
    assert dr.deployment_role_models(s)["verification"] == GEMMA


def test_operator_pins_from_settings_win_but_not_too_small_models() -> None:
    s = _s(**HYBRID, default_planning_model=QWEN, default_verification_model=GEMMA)
    dr.record_context_window(GEMMA, 1024)
    roles = dr.deployment_role_models(s)
    assert roles["planning"] == QWEN
    assert "verification" not in roles  # a pin that cannot hold a prompt is dropped


def test_env_pins_still_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_EXECUTION_MODEL", KIMI)
    assert dr.deployment_role_models(_s(**HYBRID))["execution"] == KIMI


def test_nvidia_only_serves_every_role_and_nothing_configured_is_empty() -> None:
    assert set(dr.deployment_role_models(_s(nvidia_api_key="k")).values()) == {KIMI}
    assert dr.deployment_role_models(_s()) == {}


def test_routers_apply_override_then_role_map() -> None:
    from app.agent.model_router import ModelRouter
    from app.ai_router.model_orchestrator import ModelOrchestratorAdapter

    for router in (ModelOrchestratorAdapter(), ModelRouter("openai")):
        router.set_role_map({"planning": KIMI, "execution": QWEN, "verification": QWEN})
        assert router.model_for("execution") == QWEN
        assert router.model_for("think") == KIMI
        pinned = router.with_override("agent-model")
        assert pinned.model_for("execution") == "agent-model"
        assert router.model_for("execution") == QWEN  # copy-on-write


def test_main_does_not_synthesise_role_pins(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from app.main import _apply_onprem_settings

    s = _s(**HYBRID, nvidia_base_url="https://n/v1", nvidia_embed_model="",
           embedding_base_url="", onprem_embedding_base_url="", onprem_reranker_url="",
           rag_hosted_reranker_url="", default_model="", default_llm_provider="",
           embedding_model="", onprem_embedding_model="", onprem_embedding_dim=1024,
           onprem_reranker_model="", rag_hosted_reranker_model="", embedding_dim=1024,
           nvidia_embed_dim=2048, embedding_api_key="", onprem_api_key="EMPTY",
           onprem_qwen_is_chat_default=True, onprem_disable_thinking=True)
    snapshot = dict(os.environ)
    try:
        try:
            _apply_onprem_settings(s)
        except AttributeError:
            pytest.skip("settings stub incomplete for this code path")
        for var in ("DEFAULT_PLANNING_MODEL", "DEFAULT_EXECUTION_MODEL",
                    "DEFAULT_VERIFICATION_MODEL"):
            assert not os.environ.get(var), var
    finally:
        # _apply_onprem_settings legitimately exports NVIDIA_* — do not leak it.
        os.environ.clear()
        os.environ.update(snapshot)
