"""PROV-19: model→provider comes from the configured registry; unknown is 'unknown'.

``_MODEL_PROVIDER`` listed OpenAI slugs and every unknown model mapped to
'openai', so failures of NVIDIA / on-prem models were recorded against
'openai' and health failover targeted the wrong provider. Tier models were the
static OpenAI table even when the deployment configured its own models.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai_router import model_orchestrator as mo
from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import ModelRegistry


@pytest.fixture
def reg(monkeypatch: pytest.MonkeyPatch) -> ModelRegistry:
    r = ModelRegistry()
    import app.ai_router.registry as registry_mod

    monkeypatch.setattr(registry_mod, "model_registry", r)
    r.register_configured(
        ModelEndpoint(
            provider="nvidia", model_id="moonshotai/kimi-k2", display_name="k",
            capabilities=[ModelCapability.TEXT_GENERATION], quality_score=0.9,
        )
    )
    return r


def test_nvidia_served_model_maps_to_its_configured_provider(reg: ModelRegistry) -> None:
    assert mo.provider_for_model("moonshotai/kimi-k2") == "nvidia"
    assert mo.ModelOrchestrator().provider_for_model("moonshotai/kimi-k2") == "nvidia"


def test_unknown_model_is_unknown_not_openai(reg: ModelRegistry) -> None:
    assert mo.provider_for_model("some-unlisted-model") == "unknown"


def test_known_reference_slugs_still_map(reg: ModelRegistry) -> None:
    assert mo.provider_for_model("gpt-4o") == "openai"


def test_unknown_provider_outcomes_are_not_recorded_against_openai(reg: ModelRegistry) -> None:
    adapter = mo.ModelOrchestratorAdapter()
    recorded: list[str] = []
    adapter._orchestrator.record_provider_result = (  # type: ignore[method-assign]
        lambda provider, **kw: recorded.append(provider)
    )
    adapter.record_provider_result("some-unlisted-model", ok=False)
    assert recorded == []
    adapter.record_provider_result("moonshotai/kimi-k2", ok=False)
    assert recorded == ["nvidia"]


def test_tier_models_come_from_the_configured_set(reg: ModelRegistry) -> None:
    from app.agent.pattern_config import PatternConfig

    cfg = PatternConfig(
        model_planner="", model_executor="", model_verifier="", model_classifier=""
    )
    assignment: Any = mo.ModelOrchestrator().select_models(cfg)
    assert assignment.planner == "moonshotai/kimi-k2"
    assert assignment.executor == "moonshotai/kimi-k2"
