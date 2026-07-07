"""EmbeddingPolicySelector — selects embedding model and index strategy per content type."""
from __future__ import annotations
from dataclasses import dataclass

from app.ingestion.content_classifier import ContentType


@dataclass
class EmbeddingPolicy:
    model_id: str
    dimension: int
    modality: str
    index_strategy: str
    cost_class: str


_MODALITY_MAP: dict[ContentType, tuple[str, str, int]] = {
    ContentType.TEXT: ("text", "text-embedding-3-small", 1536),
    ContentType.MARKDOWN: ("text", "text-embedding-3-small", 1536),
    ContentType.CODE: ("code", "voyage-code-3", 1024),
    ContentType.PDF: ("text", "text-embedding-3-small", 1536),
    ContentType.DOCX: ("text", "text-embedding-3-small", 1536),
    ContentType.HTML: ("text", "text-embedding-3-small", 1536),
    ContentType.IMAGE: ("multimodal", "voyage-multimodal-3", 1024),
    ContentType.AUDIO: ("text", "text-embedding-3-small", 1536),
    ContentType.VIDEO: ("multimodal", "voyage-multimodal-3", 1024),
    ContentType.CSV: ("text", "text-embedding-3-small", 1536),
    ContentType.JSON: ("text", "text-embedding-3-small", 1536),
}


class EmbeddingPolicySelector:
    def select(self, content_type: ContentType, collection_size: int = 0) -> EmbeddingPolicy:
        modality, model_id, dim = _MODALITY_MAP.get(
            content_type, ("text", "text-embedding-3-small", 1536)
        )
        index = "hnsw" if collection_size > 1000 else "exact"
        cost = "medium" if modality == "multimodal" else "low"
        return EmbeddingPolicy(
            model_id=model_id,
            dimension=dim,
            modality=modality,
            index_strategy=index,
            cost_class=cost,
        )
