# tests/embedding/test_embedding_routing.py
"""Embeddings-dimension hardening (Coverage-Matrix row 7, findings D-10/D-11/D-12).

These tests pin the *honest* and *routed* behaviour of the embedding subsystem:

* D-10 — the model chosen by ``EmbeddingOrchestrator.select`` must ACTUALLY be
  used for the physical embedding (routed to the provider), not merely written
  to metadata and discarded.
* D-11 — a "multimodal" selection for image content must honestly report that it
  is realised as caption-then-text-embed, never as a native multimodal vector.
* D-12 — a failed batch item must be surfaced (reported), never silently filled
  with a zero vector that corrupts the index.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.embedding.model_registry import EmbeddingModelRegistry, EmbeddingModelSpec
from app.embedding.orchestrator import (
    BatchEmbeddingResult,
    EmbeddingOrchestrator,
    RoutedEmbeddingResult,
)
from app.ingestion.content_classifier import ContentType
from app.providers.base import EmbedRequest, EmbedResponse
from app.tenancy.context import PlanTier, TenantContext


@pytest.fixture
def prof_ctx() -> TenantContext:
    return TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")


class RecordingProvider:
    """Embedding provider that records the model id it was asked to use."""

    provider_name = "recording"

    def __init__(self) -> None:
        self.requested_models: list[str] = []

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        self.requested_models.append(request.model)
        return EmbedResponse(
            embeddings=[[0.1, 0.2, 0.3] for _ in request.texts],
            model=request.model,
        )


# ── D-10: selection actually routes to the chosen model ──────────────────────


def _code_registry() -> EmbeddingModelRegistry:
    """The Model Registry configures a 1024-d code embedder (voyage provider)."""
    return EmbeddingModelRegistry(
        [EmbeddingModelSpec("reg-code-embed", "code", 1024, "low", "voyage")]
    )


class TestSelectionRoutes:
    async def test_embed_for_content_routes_code_to_code_model(self, prof_ctx) -> None:
        orch = EmbeddingOrchestrator(registry=_code_registry())
        default_provider = RecordingProvider()
        code_provider = RecordingProvider()

        result = await orch.embed_for_content(
            ["def foo():\n    return 1"],
            content_type=ContentType.CODE,
            tenant_ctx=prof_ctx,
            default_provider=default_provider,
            provider_resolver={"voyage/reg-code-embed": code_provider}.get,
            target_dim=1024,
        )

        assert isinstance(result, RoutedEmbeddingResult)
        # The configured code model was selected...
        assert result.model_id == "reg-code-embed"
        # ...and ACTUALLY embedded on its own (registry-built) embedder, with its
        # own model — never a model id forced onto the default embedder.
        assert code_provider.requested_models == [""]
        assert default_provider.requested_models == []
        assert result.embeddings and result.embeddings[0] == [0.1, 0.2, 0.3]

    async def test_code_is_not_routed_without_a_configured_code_model(self, prof_ctx) -> None:
        """No code embedder in the Model Registry: the default embedder, own model."""
        orch = EmbeddingOrchestrator(registry=EmbeddingModelRegistry([]))
        provider = RecordingProvider()
        result = await orch.embed_for_content(
            ["def foo(): pass"],
            content_type=ContentType.CODE,
            tenant_ctx=prof_ctx,
            default_provider=provider,
            target_dim=1024,
        )
        assert provider.requested_models == [""]
        assert result.provider == "default"

    async def test_code_model_of_another_width_is_not_routed(self, prof_ctx) -> None:
        orch = EmbeddingOrchestrator(registry=_code_registry())
        provider = RecordingProvider()
        code_provider = RecordingProvider()
        result = await orch.embed_for_content(
            ["def foo(): pass"],
            content_type=ContentType.CODE,
            tenant_ctx=prof_ctx,
            default_provider=provider,
            provider_resolver={"voyage/reg-code-embed": code_provider}.get,
            target_dim=2048,  # the collection's width differs from the code model's
        )
        assert code_provider.requested_models == []
        assert provider.requested_models == [""]
        assert result.provider == "default"

    async def test_embed_for_content_text_does_not_use_code_model(self, prof_ctx) -> None:
        orch = EmbeddingOrchestrator(registry=_code_registry())
        provider = RecordingProvider()

        result = await orch.embed_for_content(
            ["Plain english prose about the platform."],
            content_type=ContentType.TEXT,
            tenant_ctx=prof_ctx,
            default_provider=provider,
            target_dim=1024,
        )

        assert result.model_id != "reg-code-embed"
        assert provider.requested_models == [""]

    async def test_embed_for_content_resolves_provider_by_registry_key(self, prof_ctx) -> None:
        orch = EmbeddingOrchestrator(registry=_code_registry())
        default_provider = RecordingProvider()
        routed_provider = RecordingProvider()

        seen: list[str] = []

        def resolver(key: str):
            seen.append(key)
            return routed_provider

        result = await orch.embed_for_content(
            ["def foo(): pass"],
            content_type=ContentType.CODE,
            tenant_ctx=prof_ctx,
            default_provider=default_provider,
            provider_resolver=resolver,
            target_dim=1024,
        )

        # The Model Registry key of the selected model is what the resolver saw.
        assert seen == ["voyage/reg-code-embed"]
        assert routed_provider.requested_models == [""]
        assert default_provider.requested_models == []
        assert result.provider == "voyage"

    async def test_unbuildable_route_falls_back_to_the_default(self, prof_ctx) -> None:
        orch = EmbeddingOrchestrator(registry=_code_registry())
        provider = RecordingProvider()
        result = await orch.embed_for_content(
            ["def foo(): pass"],
            content_type=ContentType.CODE,
            tenant_ctx=prof_ctx,
            default_provider=provider,
            provider_resolver=lambda _key: None,
            target_dim=1024,
        )
        assert provider.requested_models == [""]
        assert result.provider == "default"

    async def test_embed_for_content_falls_back_to_default_when_no_provider(
        self, prof_ctx
    ) -> None:
        """No provider available → empty-vector sentinel, never a crash."""
        orch = EmbeddingOrchestrator()
        result = await orch.embed_for_content(
            ["a", "b"],
            content_type=ContentType.TEXT,
            tenant_ctx=prof_ctx,
            default_provider=None,
        )
        # Empty sentinel (never zero vectors) — consistent with providers.base.embed_texts.
        assert result.embeddings == [[], []]


# ── D-11: multimodal is honest (caption-then-text-embed) ─────────────────────


class TestMultimodalHonesty:
    def test_image_selection_flagged_as_caption_first(self, prof_ctx) -> None:
        orch = EmbeddingOrchestrator()
        selection = orch.select(content_type=ContentType.IMAGE, tenant_ctx=prof_ctx)
        # We cannot produce a native multimodal vector; image embedding is
        # realised as caption-then-text-embed. The selection must say so.
        assert selection.requires_captioning is True

    def test_text_selection_not_caption_first(self, prof_ctx) -> None:
        orch = EmbeddingOrchestrator()
        selection = orch.select(content_type=ContentType.TEXT, tenant_ctx=prof_ctx)
        assert selection.requires_captioning is False

    async def test_embed_for_content_image_reports_text_of_caption(self, prof_ctx) -> None:
        orch = EmbeddingOrchestrator()
        provider = RecordingProvider()
        result = await orch.embed_for_content(
            ["A caption describing the image."],
            content_type=ContentType.IMAGE,
            tenant_ctx=prof_ctx,
            default_provider=provider,
        )
        assert result.embedding_input == "text_of_caption"


# ── D-12: failed batch items are surfaced, never zero-filled ─────────────────


class OKProvider:
    provider_name = "ok-provider"


class TestBatchFailureSurfaced:
    async def test_failed_item_is_reported_and_not_zero_vector(self) -> None:
        orch = EmbeddingOrchestrator()

        async def mock_embed(texts: list[str], provider: object) -> list[list[float]]:
            if "FAIL" in texts:
                raise RuntimeError("provider boom")
            return [[1.0, 2.0] for _ in texts]

        with patch("app.providers.base.embed_texts", side_effect=mock_embed):
            result = await orch.embed_batch(
                ["ok", "FAIL", "ok2"],
                providers=[OKProvider()],
                batch_size=1,
            )

        assert isinstance(result, BatchEmbeddingResult)
        # The failed index is surfaced.
        assert result.failed_indices == [1]
        # The failed slot is the None sentinel, NOT a silent zero vector.
        assert result.embeddings[1] is None
        # No slot is a non-empty all-zeros vector (the old corruption bug).
        assert not any(v and all(x == 0.0 for x in v) for v in result.embeddings)
        # Successful slots carry real vectors.
        assert result.embeddings[0] == [1.0, 2.0]
        assert result.embeddings[2] == [1.0, 2.0]
        assert result.errors  # error text recorded too

    async def test_all_ok_batch_has_no_failed_indices(self) -> None:
        orch = EmbeddingOrchestrator()

        async def mock_embed(texts: list[str], provider: object) -> list[list[float]]:
            return [[1.0, 2.0] for _ in texts]

        with patch("app.providers.base.embed_texts", side_effect=mock_embed):
            result = await orch.embed_batch(
                ["a", "b", "c"], providers=[OKProvider()], batch_size=2
            )

        assert result.failed_indices == []
        assert all(v == [1.0, 2.0] for v in result.embeddings)


# ── VectorIndexPolicy dimension guard wiring (secondary) ─────────────────────


class TestDimensionGuard:
    def test_guard_dimension_raises_on_mismatch(self) -> None:
        orch = EmbeddingOrchestrator()
        with pytest.raises(ValueError):
            orch.guard_dimension_change(existing_dim=1536, new_dim=3072)

    def test_guard_dimension_allows_match(self) -> None:
        orch = EmbeddingOrchestrator()
        # Same dimension → no raise, returns True.
        assert orch.guard_dimension_change(existing_dim=1024, new_dim=1024) is True
