"""Seeder regression: vision/OCR are seeded only when explicitly pinned, and a
pin naming an already-seeded model MERGES capabilities instead of replacing it.

The bug: with ``VISION_MODEL`` unset the vision model defaulted to the reasoning
model and was re-registered as ``[TG, VISION, OCR]`` with ``supports_tools=False``
— replacing the reasoning entry and removing it from step execution.
"""

from __future__ import annotations

_ISOLATE_PROVIDER_ENV = True

from collections.abc import Iterator

import pytest

from app.ai_router.models import ModelCapability, TaskType
from app.ai_router.registry import ModelRegistry
from app.ai_router.seeder import seed_registry_from_config
from app.ai_router.selection import ordered_configured_models

_TG, _TU, _SO = (
    ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_USE, ModelCapability.STRUCTURED_OUTPUT
)
_VI, _OC = ModelCapability.VISION, ModelCapability.OCR

REASONING = "reasoning-llm"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import app.ai_router.registry_store as store_mod

    for name in ("VISION_MODEL", "NVIDIA_VISION_MODEL", "OCR_MODEL", "NVIDIA_MODEL",
                 "OPENAI_MODEL", "RAG_HOSTED_RERANKER_MODEL", "ONPREM_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DEFAULT_MODEL", REASONING)
    monkeypatch.setattr(store_mod, "_store", None)  # no persisted overrides
    yield


def _entries(reg: ModelRegistry, model_id: str) -> list:
    return [m for m in reg.list_configured() if m.model_id == model_id]


def test_no_vision_pin_seeds_no_vision_model_and_keeps_the_reasoning_entry() -> None:
    reg = ModelRegistry()
    seed_registry_from_config(reg)
    [entry] = _entries(reg, REASONING)
    assert set(entry.capabilities) == {_TG, _TU, _SO}
    assert entry.supports_tools is True
    assert reg.list_configured(_VI) == []
    assert reg.list_configured(_OC) == []
    assert [m.model_id for m in ordered_configured_models(TaskType.EXECUTION, registry=reg)] == [
        REASONING
    ]


def test_vision_pin_equal_to_the_reasoning_model_merges_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VISION_MODEL", REASONING)
    reg = ModelRegistry()
    seed_registry_from_config(reg)
    [entry] = _entries(reg, REASONING)
    assert {_TG, _TU, _SO, _VI, _OC} <= set(entry.capabilities)
    assert entry.supports_tools is True
    assert entry.supports_vision is True
    assert entry.supports_structured_output is True
    # Still the step executor, and now the vision model too.
    assert [m.model_id for m in ordered_configured_models(TaskType.EXECUTION, registry=reg)] == [
        REASONING
    ]
    assert [
        m.model_id
        for m in ordered_configured_models(TaskType.VISION, require_vision=True, registry=reg)
    ] == [REASONING]


def test_dedicated_vision_and_ocr_pins_are_seeded_without_text_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VISION_MODEL", "dedicated-vlm")
    monkeypatch.setenv("OCR_MODEL", "dedicated-ocr")
    reg = ModelRegistry()
    seed_registry_from_config(reg)
    [vlm] = _entries(reg, "dedicated-vlm")
    [ocr] = _entries(reg, "dedicated-ocr")
    assert set(vlm.capabilities) == {_VI, _OC} and vlm.supports_vision
    assert set(ocr.capabilities) == {_OC}
    assert vlm.extra.get("source") == "env"
    # A dedicated VLM is never a planning candidate.
    assert [m.model_id for m in ordered_configured_models(TaskType.PLANNING, registry=reg)] == [
        REASONING
    ]
