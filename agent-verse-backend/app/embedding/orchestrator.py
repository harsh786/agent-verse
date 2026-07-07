"""EmbeddingOrchestrator — selects embedding model per content type and tenant policy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.embedding.model_registry import EmbeddingModelRegistry
from app.embedding.dimension_policy import DimensionPolicy
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


@dataclass
class EmbeddingSelectionResult:
    model_id: str
    dimension: int
    modality: str
    cost_class: str
    provider: str
    selection_reason: str = ""


class EmbeddingOrchestrator:
    def __init__(self, registry: EmbeddingModelRegistry | None = None) -> None:
        self._registry = registry or EmbeddingModelRegistry.build_default()
        self._dim_policy = DimensionPolicy()

    def select(
        self,
        content_type: ContentType,
        tenant_ctx: "TenantContext",
    ) -> EmbeddingSelectionResult:
        modalities = _MODALITY_MAP.get(content_type, ["text"])
        allowed_costs = _COST_BY_PLAN.get(tenant_ctx.plan.value, ["low"])

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
