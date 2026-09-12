"""Generic cost-aware model selection over CONFIGURED models (P1: reasoning)."""

from __future__ import annotations

import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint, RoutingMode, TaskType
from app.ai_router.registry import ModelRegistry
from app.ai_router.router import AIRouter
from app.ai_router.seeder import seed_registry_from_config
from app.ai_router.selection import select_configured_model_id

_TG = ModelCapability.TEXT_GENERATION
_TU = ModelCapability.TOOL_USE


def _reg() -> ModelRegistry:
    return ModelRegistry()


def _reasoning(model_id: str, cost: float, *, tools: bool = True, quality: float = 0.7):
    return ModelEndpoint(
        provider="test",
        model_id=model_id,
        display_name=model_id,
        capabilities=[_TG, _TU] if tools else [_TG],
        cost_per_1k_input=cost,
        supports_tools=tools,
        quality_score=quality,
        is_available=True,
    )


# ── seeder ───────────────────────────────────────────────────────────────────


def test_seeder_registers_configured_reasoning_model(monkeypatch):
    for v in ("DEFAULT_PLANNING_MODEL", "DEFAULT_EXECUTION_MODEL", "DEFAULT_VERIFICATION_MODEL",
              "DEFAULT_MODEL", "OPENAI_MODEL", "NVIDIA_EMBED_MODEL", "EMBEDDING_MODEL",
              "VISION_MODEL", "OCR_MODEL", "NVIDIA_VISION_MODEL", "RAG_HOSTED_RERANKER_MODEL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "x")
    monkeypatch.setenv("NVIDIA_MODEL", "openai/gpt-oss-20b")
    reg = _reg()
    n = seed_registry_from_config(reg)
    assert n >= 1
    ids = {m.model_id for m in reg.list_configured(_TG)}
    assert "openai/gpt-oss-20b" in ids
    # self-hosted slug -> unpriced (cost 0.0)
    m = next(m for m in reg.list_configured() if m.model_id == "openai/gpt-oss-20b")
    assert m.cost_per_1k_input == 0.0


def test_seeder_prices_known_cloud_slug(monkeypatch):
    for v in ("NVIDIA_MODEL", "OPENAI_MODEL"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("DEFAULT_MODEL", "gpt-4o-mini")
    reg = _reg()
    seed_registry_from_config(reg)
    m = next(m for m in reg.list_configured() if m.model_id == "gpt-4o-mini")
    assert m.cost_per_1k_input > 0.0  # priced from the reference catalog


# ── selection ────────────────────────────────────────────────────────────────


def test_single_configured_model_is_returned():
    reg = _reg()
    reg.register_configured(_reasoning("only-model", 0.0))
    assert select_configured_model_id(TaskType.PLANNING, registry=reg) == "only-model"


def test_cheapest_wins_when_multiple_configured():
    reg = _reg()
    reg.register_configured(_reasoning("pricey", 0.01))
    reg.register_configured(_reasoning("cheap", 0.001))
    reg.register_configured(_reasoning("free", 0.0))
    assert select_configured_model_id(TaskType.EXECUTION, registry=reg) == "free"


def test_cost_tie_broken_by_quality():
    reg = _reg()
    reg.register_configured(_reasoning("a", 0.0, quality=0.6))
    reg.register_configured(_reasoning("b", 0.0, quality=0.9))
    assert select_configured_model_id(TaskType.VERIFICATION, registry=reg) == "b"


def test_require_tools_filters_out_non_tool_models():
    reg = _reg()
    reg.register_configured(_reasoning("no-tools", 0.0, tools=False))
    assert select_configured_model_id(TaskType.PLANNING, require_tools=True, registry=reg) == ""
    # without the requirement it is selectable
    assert select_configured_model_id(TaskType.PLANNING, registry=reg) == "no-tools"


def test_empty_registry_returns_fallback_signal():
    assert select_configured_model_id(TaskType.PLANNING, registry=_reg()) == ""


def test_role_string_aliases_resolve():
    reg = _reg()
    reg.register_configured(_reasoning("m", 0.0))
    for role in ("planning", "execution", "verification", "reflection", "think"):
        assert select_configured_model_id(role, registry=reg) == "m"


# ── router CHEAPEST tie-break ─────────────────────────────────────────────────


def test_airouter_cheapest_prefers_zero_cost_then_quality(monkeypatch):
    from app.ai_router.models import ModelRoutePolicy

    reg = ModelRegistry()
    reg._models = {  # replace catalog with a controlled set
        "t/free": _reasoning("free", 0.0, quality=0.5),
        "t/free-better": _reasoning("free-better", 0.0, quality=0.9),
        "t/paid": _reasoning("paid", 0.01, quality=0.99),
    }
    reg.set_route_policy(
        "t1", TaskType.EXECUTION,
        ModelRoutePolicy(task_type=TaskType.EXECUTION, routing_mode=RoutingMode.CHEAPEST),
    )
    import app.ai_router.router as rmod

    monkeypatch.setattr(rmod, "model_registry", reg)  # auto-restored after the test
    chosen = AIRouter().select_model(TaskType.EXECUTION, "t1")
    assert chosen is not None and chosen.model_id == "free-better"
