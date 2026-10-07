"""LlmStructuredExtractor sends the extraction role's reasoning model — never the
literal model id ``"default"`` (which no endpoint serves)."""

from __future__ import annotations

_ISOLATE_PROVIDER_ENV = True

from collections.abc import Iterator
from typing import Any

import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry
from app.ocr.extractors.general import LlmStructuredExtractor
from app.providers.base import CompletionResponse

_TG, _TU = ModelCapability.TEXT_GENERATION, ModelCapability.TOOL_USE


class _Recording:
    _default_model = "provider-default"

    def __init__(self, broken: set[str] | None = None) -> None:
        self.models: list[str] = []
        self.broken = broken or set()

    async def complete(self, request: Any) -> CompletionResponse:
        self.models.append(request.model)
        if request.model in self.broken:
            raise RuntimeError(f"{request.model} is down")
        return CompletionResponse(
            content='{"invoice_number": {"value": "INV-7", "confidence": 0.9}}',
            model=request.model, input_tokens=1, output_tokens=1,
        )


def _add(model_id: str, cost: float) -> None:
    model_registry.register_configured(
        ModelEndpoint(provider="custom", model_id=model_id, display_name=model_id,
                      capabilities=[_TG, _TU], supports_tools=True, cost_per_1k_input=cost,
                      extra={"source": "env"})
    )


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    import app.ai_router.selection as sel
    from app.providers import guarded_completion

    async def _noop(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(guarded_completion, "_charge", _noop)
    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    monkeypatch.delenv("DEFAULT_EXECUTION_MODEL", raising=False)
    model_registry.clear_configured()
    model_registry.set_preferences({})
    yield
    model_registry.clear_configured()
    model_registry.set_preferences({})


async def test_extraction_uses_the_registry_reasoning_order_with_failover() -> None:
    from app.providers.model_dispatch import ModelDispatchProvider

    _add("llm-a", 0.0)
    _add("llm-b", 0.1)
    model_registry.set_preferences({"text_generation": ["custom/llm-a", "custom/llm-b"]})
    inner = _Recording(broken={"llm-a"})

    fields = await LlmStructuredExtractor(ModelDispatchProvider(inner)).extract_async(
        "Invoice INV-7"
    )

    assert fields["invoice_number"].value == "INV-7"
    assert "default" not in inner.models
    assert inner.models[:2] == ["llm-a", "llm-b"]


async def test_without_a_saved_order_the_provider_keeps_its_own_default() -> None:
    inner = _Recording()
    fields = await LlmStructuredExtractor(inner).extract_async("Invoice INV-7")
    assert fields["invoice_number"].value == "INV-7"
    # The provider's own default model — never the literal "default".
    assert inner.models == ["provider-default"]
