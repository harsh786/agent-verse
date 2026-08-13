"""EmbeddingOrchestrator — selects embedding model per content type and tenant policy."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.embedding.dimension_policy import DimensionPolicy
from app.embedding.model_registry import EmbeddingModelRegistry
from app.ingestion.content_classifier import ContentType

if TYPE_CHECKING:
    from app.tenancy.context import TenantContext

_MODALITY_MAP: dict[ContentType, list[str]] = {
    ContentType.TEXT: ["text"],
    ContentType.MARKDOWN: ["text"],
    ContentType.CODE: ["code", "text"],
    ContentType.PDF: ["text"],
    ContentType.DOCX: ["text"],
    ContentType.HTML: ["text"],
    ContentType.IMAGE: ["multimodal", "image", "text"],
    ContentType.AUDIO: ["text"],  # transcript embedding
    ContentType.VIDEO: ["multimodal", "text"],
    ContentType.CSV: ["text"],
    ContentType.JSON: ["text"],
}

_COST_BY_PLAN = {
    "free": ["free", "low"],
    "starter": ["free", "low"],
    "professional": ["free", "low", "medium"],
    "enterprise": ["free", "low", "medium", "high"],
}

# Provider fallback order — used when the primary provider fails.
# Each entry is a provider name; the orchestrator tries them in order.
_FALLBACK_ORDER = ["anthropic", "openai", "voyage", "gemini", "fake"]

# Default batch size for embed_batch()
_DEFAULT_BATCH_SIZE = 32


@dataclass
class EmbeddingSelectionResult:
    model_id: str
    dimension: int
    modality: str
    cost_class: str
    provider: str
    selection_reason: str = ""


@dataclass
class BatchEmbeddingResult:
    embeddings: list[list[float]]
    model_id: str
    provider: str
    errors: list[str] = field(default_factory=list)


class EmbeddingOrchestrator:
    def __init__(self, registry: EmbeddingModelRegistry | None = None) -> None:
        self._registry = registry or EmbeddingModelRegistry.build_default()
        self._dim_policy = DimensionPolicy()

    def select(
        self,
        content_type: ContentType,
        tenant_ctx: TenantContext | None = None,
        collection_size: int = 0,
    ) -> EmbeddingSelectionResult:
        modalities = _MODALITY_MAP.get(content_type, ["text"])
        # Use tenant plan when available; fall back to "free" tier for broad compatibility
        if tenant_ctx is not None:
            allowed_costs = _COST_BY_PLAN.get(tenant_ctx.plan.value, ["low"])
        else:
            allowed_costs = _COST_BY_PLAN.get("free", ["free", "low"])

        # Try each modality in preference order
        for modality in modalities:
            candidates = self._registry.list_by_modality(modality)
            # Filter by allowed cost class
            affordable = [c for c in candidates if c.cost_class in allowed_costs]
            if affordable:
                # Pick highest quality (largest dimension) that's affordable
                best = max(affordable, key=lambda m: m.dimension)
                return EmbeddingSelectionResult(
                    model_id=best.model_id,
                    dimension=self._dim_policy.select(best.model_id),
                    modality=best.modality,
                    cost_class=best.cost_class,
                    provider=best.provider,
                    selection_reason=f"content_type={content_type.value} modality={modality}",
                )

        # Fallback to any text model
        fallback = self._registry.list_by_modality("text")
        if fallback:
            m = fallback[0]
            return EmbeddingSelectionResult(
                model_id=m.model_id,
                dimension=self._dim_policy.select(m.model_id),
                modality=m.modality,
                cost_class=m.cost_class,
                provider=m.provider,
                selection_reason="fallback to text embedding",
            )

        # Ultimate fallback
        return EmbeddingSelectionResult(
            model_id="fake-embedding",
            dimension=10,
            modality="text",
            cost_class="free",
            provider="fake",
            selection_reason="no embedding model available",
        )

    async def embed_with_fallback(
        self,
        text: str,
        providers: list[Any],
        model_id: str = "",
    ) -> list[float]:
        """Embed *text* trying each provider in *providers* until one succeeds.

        Providers are tried in the order given. On failure the next provider in
        the list is attempted. Raises RuntimeError if all providers fail.
        """
        last_exc: Exception | None = None
        for provider in providers:
            try:
                from app.providers.base import embed_texts as _embed
                result = await _embed([text], provider=provider)
                if result:
                    return result[0]
            except Exception as exc:
                last_exc = exc
                continue
        raise RuntimeError(
            f"All embedding providers failed. Last error: {last_exc}"
        ) from last_exc

    async def embed_batch(
        self,
        texts: list[str],
        providers: list[Any],
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> BatchEmbeddingResult:
        """Embed *texts* in batches of *batch_size*, with provider fallback per batch.

        Returns embeddings in the same order as *texts*. Failed batches are
        retried with the next provider; errors for individual batches are
        recorded in the result.
        """
        if not texts:
            return BatchEmbeddingResult(embeddings=[], model_id="", provider="none")

        all_embeddings: list[list[float]] = [[] for _ in texts]
        errors: list[str] = []
        num_batches = math.ceil(len(texts) / batch_size)
        used_provider = "unknown"
        used_model = ""

        for batch_idx in range(num_batches):
            start = batch_idx * batch_size
            end = min(start + batch_size, len(texts))
            batch_texts = texts[start:end]

            batch_ok = False
            for provider in providers:
                try:
                    from app.providers.base import embed_texts as _embed
                    result = await _embed(batch_texts, provider=provider)
                    for i, emb in enumerate(result):
                        all_embeddings[start + i] = emb
                    used_provider = getattr(provider, "provider_name", str(provider))
                    batch_ok = True
                    break
                except Exception as exc:
                    errors.append(f"batch_{batch_idx}: {exc!s}")
                    continue

            if not batch_ok:
                # Fill with empty vectors so downstream code can handle gracefully
                dim = 10
                for i in range(len(batch_texts)):
                    all_embeddings[start + i] = [0.0] * dim

        return BatchEmbeddingResult(
            embeddings=all_embeddings,
            model_id=used_model,
            provider=used_provider,
            errors=errors,
        )
