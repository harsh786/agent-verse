"""Register Model Registry vision models for a test (the vision chain)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from app.ai_router.models import ModelCapability, ModelEndpoint
from app.ai_router.registry import model_registry


@contextmanager
def vision_models(monkeypatch: Any, *models: tuple[str, str]) -> Iterator[None]:
    """``(provider, model_id)`` vision models, in chain order (cheapest first)."""
    import app.ai_router.selection as sel

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    for name in ("VISION_MODEL", "NVIDIA_VISION_MODEL"):
        monkeypatch.delenv(name, raising=False)
    model_registry.clear_configured()
    for i, (provider, model_id) in enumerate(models):
        model_registry.register_configured(
            ModelEndpoint(
                provider=provider, model_id=model_id, display_name=model_id,
                capabilities=[ModelCapability.VISION], supports_vision=True,
                cost_per_1k_input=0.001 * i, extra={"source": "env"},
            )
        )
    try:
        yield
    finally:
        model_registry.clear_configured()


@pytest.fixture
def registry_vision(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A Model Registry vision model (env-seeded, no own endpoint).

    Whether the browser agent can see is decided by the registry; with no own
    ``base_url`` the dispatch provider sends the call to the injected provider,
    so a test's scripted provider still answers it.
    """
    import app.ai_router.selection as sel
    from app.ai_router.models import ModelCapability, ModelEndpoint
    from app.ai_router.registry import model_registry

    monkeypatch.setattr(sel, "_lazy_seeded", True)
    monkeypatch.setattr(sel, "_last_version_check", float("inf"))
    model_registry.clear_configured()
    model_registry.register_configured(
        ModelEndpoint(
            provider="openai", model_id="registry-vlm", display_name="registry-vlm",
            capabilities=[ModelCapability.VISION], supports_vision=True,
            extra={"source": "env"},
        )
    )
    yield "registry-vlm"
    model_registry.clear_configured()
