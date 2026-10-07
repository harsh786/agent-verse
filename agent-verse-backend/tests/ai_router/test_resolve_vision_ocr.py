"""resolve_vision / resolve_ocr: the Model Registry decides, env pins follow,
nothing configured is an honest ModelNotConfiguredError (never the reasoning
model, never a vendor literal)."""

from __future__ import annotations

_ISOLATE_PROVIDER_ENV = True

from collections.abc import Iterator

import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.ai_router.resolve import (
    TESSERACT_MODEL,
    ModelNotConfiguredError,
    resolve_ocr,
    resolve_vision,
    vision_configured,
)

_TG, _TU = ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_USE
_VI, _OC = ModelCapability.VISION, ModelCapability.OCR

_PIN_ENV = (
    "VISION_MODEL", "NVIDIA_VISION_MODEL", "OCR_MODEL", "OLLAMA_OCR_MODEL", "OLLAMA_BASE_URL",
    "OCR_TESSERACT_ENABLED", "NVIDIA_MODEL", "DEFAULT_MODEL", "OPENAI_MODEL",
)


def _add(model_id: str, caps: list[ModelCapability], *, cost: float = 0.0,
         vision: bool | None = None, base_url: str | None = None,
         provider: str = "custom", source: str = "override") -> None:
    model_registry.register_configured(
        ModelEndpoint(
            provider=provider, model_id=model_id, display_name=model_id, capabilities=caps,
            cost_per_1k_input=cost, supports_vision=(_VI in caps) if vision is None else vision,
            supports_tools=_TU in caps, base_url=base_url,
            extra={"source": source, "origin": "manual"},
        )
    )


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    for name in _PIN_ENV:
        monkeypatch.delenv(name, raising=False)
    # A registry override is eligible when it names its own endpoint.
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


# ── vision ───────────────────────────────────────────────────────────────────


def test_vision_registry_order_wins_and_carries_provider_and_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VISION_MODEL", "env-vlm")
    _add("cheap-vlm", [_VI], cost=0.0, base_url="http://vlm-a.test/v1")
    _add("pricey-vlm", [_VI], cost=0.5, base_url="http://vlm-b.test/v1")
    res = resolve_vision()
    assert res.model == "cheap-vlm"
    assert res.source == "registry_cheapest"
    assert res.provider == "custom"
    assert res.base_url == "http://vlm-a.test/v1"
    # The rest of the registry vision order, then the env pin.
    assert res.fallbacks == ("pricey-vlm", "env-vlm")

    model_registry.set_preferences({"vision": ["custom/pricey-vlm"]})
    res = resolve_vision()
    assert (res.model, res.source) == ("pricey-vlm", "registry_preference")
    assert res.fallbacks == ("cheap-vlm", "env-vlm")


def test_vision_skips_models_without_vision_support() -> None:
    # Tagged "vision" but not able to take images, and an OCR-only model: neither
    # can caption an image.
    _add("no-images", [_VI], vision=False, base_url="http://x.test/v1")
    _add("ocr-only", [_OC], base_url="http://y.test/v1")
    with pytest.raises(ModelNotConfiguredError) as exc:
        resolve_vision()
    assert exc.value.capability == "vision"
    assert "VISION_MODEL" in exc.value.hint


def test_vision_env_pin_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_VISION_MODEL", "nvidia/env-vlm")
    res = resolve_vision()
    assert (res.model, res.source, res.fallbacks) == ("nvidia/env-vlm", "env_pin", ())


def test_vision_never_falls_back_to_the_reasoning_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.model_defaults import configured_vision_model

    monkeypatch.setenv("NVIDIA_MODEL", "reasoning-llm")
    monkeypatch.setenv("DEFAULT_MODEL", "reasoning-llm")
    _add("reasoning-llm", [_TG, _TU], base_url="http://llm.test/v1")
    assert configured_vision_model("") == ""
    assert not vision_configured()
    with pytest.raises(ModelNotConfiguredError):
        resolve_vision()


def test_env_seeded_vision_model_reports_env_pin() -> None:
    _add("seeded-vlm", [_VI, _OC], provider="nvidia", source="env")
    res = resolve_vision()
    assert (res.model, res.source, res.provider) == ("seeded-vlm", "env_pin", "nvidia")


# ── ocr ──────────────────────────────────────────────────────────────────────


def test_ocr_order_registry_ocr_then_vision_then_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_MODEL", "env-ocr")
    _add("vlm", [_VI], base_url="http://v.test/v1")
    _add("ocr-a", [_OC], cost=0.2, base_url="http://o.test/v1")
    _add("ocr-b", [_OC], cost=0.1, base_url="http://o2.test/v1")
    res = resolve_ocr()
    assert res.model == "ocr-b"
    assert res.base_url == "http://o2.test/v1"
    assert res.fallbacks == ("ocr-a", "vlm", "env-ocr")


def test_ocr_falls_back_to_registry_vision_models() -> None:
    _add("vlm", [_VI], base_url="http://v.test/v1")
    res = resolve_ocr()
    assert (res.model, res.capability) == ("vlm", "ocr")


def test_ocr_env_pins_then_tesseract_then_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCR_MODEL", "env-ocr")
    monkeypatch.setenv("OLLAMA_OCR_MODEL", "local-ocr-vlm")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama.test:11434")
    res = resolve_ocr()
    assert (res.model, res.source, res.fallbacks) == ("env-ocr", "env_pin", ("local-ocr-vlm",))

    monkeypatch.delenv("OCR_MODEL")
    res = resolve_ocr()
    assert (res.model, res.provider, res.base_url) == (
        "local-ocr-vlm", "ollama", "http://ollama.test:11434"
    )

    monkeypatch.delenv("OLLAMA_OCR_MODEL")
    monkeypatch.setenv("OCR_TESSERACT_ENABLED", "true")
    res = resolve_ocr()
    assert (res.model, res.source, res.provider) == (TESSERACT_MODEL, "local_default", "local")

    monkeypatch.setenv("OCR_TESSERACT_ENABLED", "false")
    with pytest.raises(ModelNotConfiguredError) as exc:
        resolve_ocr()
    assert exc.value.capability == "ocr"
    assert "OCR_TESSERACT_ENABLED" in exc.value.hint


def test_ocr_selection_helpers_never_return_the_reasoning_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.ai_router.selection import resolve_ocr_fallback_models, resolve_ocr_model

    monkeypatch.setenv("DEFAULT_MODEL", "reasoning-llm")
    monkeypatch.setenv("OCR_TESSERACT_ENABLED", "true")
    assert resolve_ocr_model("") == ""  # Tesseract is the engine's tier, not a model id
    assert resolve_ocr_fallback_models("x") == []
