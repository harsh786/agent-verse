"""Regression: embedding fallbacks that silently produced fake vectors.

* ``EmbeddingRouter.embed_texts`` defaulted to ``fallback_lexical=True``: any
  caller that forgot to opt out got 384-dim hashing vectors on provider failure.
* ``EmbeddingOrchestrator.select``'s ultimate fallback returned the 10-dim
  ``fake-embedding`` in every environment, and the registry offered it too.
* ``_FALLBACK_ORDER`` listed ``anthropic``, which has no embeddings API.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.embedding.model_registry import EmbeddingModelRegistry
from app.embedding.orchestrator import (
    _FALLBACK_ORDER,
    EmbeddingOrchestrator,
    fallback_order,
)
from app.embedding.router import EmbeddingRouter, EmbeddingUnavailableError
from app.ingestion.content_classifier import ContentType


class _Broken:
    async def embed(self, request: Any) -> Any:
        raise RuntimeError("down")


async def test_router_defaults_to_failing_not_fake_vectors() -> None:
    with pytest.raises(EmbeddingUnavailableError):
        await EmbeddingRouter(provider=_Broken()).embed_texts(["hello"])
    with pytest.raises(EmbeddingUnavailableError):
        await EmbeddingRouter().embed_texts(["hello"])


async def test_router_opt_in_fallback_is_reported() -> None:
    result = await EmbeddingRouter(provider=_Broken()).embed_texts_report(
        ["hello"], fallback_lexical=True
    )
    assert result.used_fallback is True
    assert result.model == "lexical-hash-384"
    assert len(result.embeddings[0]) == 384


def test_anthropic_is_not_an_embeddings_fallback() -> None:
    assert "anthropic" not in _FALLBACK_ORDER
    assert "anthropic" not in fallback_order()


def test_fake_is_never_in_the_production_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert "fake" not in fallback_order()
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert fallback_order()[-1] == "fake"


@pytest.mark.parametrize("environment", ["production", "development"])
def test_empty_registry_selects_the_default_embedder_never_a_fake_model(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    orch = EmbeddingOrchestrator(registry=EmbeddingModelRegistry([]))
    selection = orch.select(ContentType.TEXT)
    assert selection.uses_default_embedder
    assert selection.model_id == ""  # the default embedder's own model, no literal


@pytest.mark.parametrize("environment", ["production", "development"])
def test_routing_registry_has_no_fake_model(
    monkeypatch: pytest.MonkeyPatch, environment: str
) -> None:
    from app.ai_router.registry import ModelRegistry

    monkeypatch.setenv("ENVIRONMENT", environment)
    registry = EmbeddingModelRegistry.from_model_registry(registry=ModelRegistry())
    assert registry.get("fake-embedding") is None
    assert registry.list_all() == []
