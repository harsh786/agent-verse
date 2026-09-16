"""EmbeddingModelRegistry — catalogue of available embedding models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EmbeddingModelSpec:
    model_id: str
    modality: str  # text|code|multimodal|image
    dimension: int
    cost_class: str  # free|low|medium|high
    provider: str
    description: str = ""
    max_input_tokens: int = 8192


class EmbeddingModelRegistry:
    def __init__(self, models: list[EmbeddingModelSpec]) -> None:
        self._by_id = {m.model_id: m for m in models}

    def get(self, model_id: str) -> EmbeddingModelSpec | None:
        return self._by_id.get(model_id)

    def list_by_modality(self, modality: str) -> list[EmbeddingModelSpec]:
        return [m for m in self._by_id.values() if m.modality == modality]

    def list_all(self) -> list[EmbeddingModelSpec]:
        return list(self._by_id.values())

    def filter(self, *, cost_class: str | None = None) -> list[EmbeddingModelSpec]:
        results = list(self._by_id.values())
        if cost_class:
            results = [m for m in results if m.cost_class == cost_class]
        return results

    @classmethod
    def build_default(cls) -> EmbeddingModelRegistry:
        import os

        # When a dedicated embedding model is configured (EMBEDDING_MODEL /
        # NVIDIA_EMBED_MODEL — e.g. an NVIDIA or on-prem OpenAI-compatible endpoint),
        # advertise ONLY that model for text. Otherwise select() would pick the
        # largest catalogue model (e.g. text-embedding-3-large) and the configured
        # provider 404s on a model it does not serve, breaking all ingestion.
        configured = (
            os.getenv("EMBEDDING_MODEL", "").strip()
            or os.getenv("NVIDIA_EMBED_MODEL", "").strip()
        )
        if configured:
            try:
                dim = int(os.getenv("EMBEDDING_DIM", "").strip() or 0)
            except ValueError:
                dim = 0
            provider = os.getenv("EMBEDDING_PROVIDER", "").strip() or "openai"
            return cls(
                [
                    # Only the TEXT models are replaced with the configured one (so a
                    # single-model provider isn't handed a text-embedding-3-* id it
                    # can't serve). Code/multimodal routing is left to the catalogue.
                    EmbeddingModelSpec(
                        configured,
                        "text",
                        dim or 1024,
                        "low",
                        provider,
                        "Configured embedding model (EMBEDDING_MODEL)",
                    ),
                    # Kept only as the free-tier / no-provider fallback; never wins
                    # over the configured model on dimension.
                    EmbeddingModelSpec(
                        "fake-embedding", "text", 10, "free", "fake", "Fake embedding for testing"
                    ),
                    EmbeddingModelSpec(
                        "voyage-code-3", "code", 1024, "low", "voyage", "Voyage code embedding"
                    ),
                    EmbeddingModelSpec(
                        "voyage-multimodal-3",
                        "multimodal",
                        1024,
                        "medium",
                        "voyage",
                        "Voyage multimodal embedding",
                    ),
                ]
            )
        return cls(
            [
                EmbeddingModelSpec(
                    "text-embedding-3-small",
                    "text",
                    1536,
                    "low",
                    "openai",
                    "OpenAI small text embedding",
                ),
                EmbeddingModelSpec(
                    "text-embedding-3-large",
                    "text",
                    3072,
                    "medium",
                    "openai",
                    "OpenAI large text embedding",
                ),
                EmbeddingModelSpec(
                    "voyage-3-lite", "text", 512, "low", "voyage", "Voyage text embedding lite"
                ),
                EmbeddingModelSpec(
                    "voyage-code-3", "code", 1024, "low", "voyage", "Voyage code embedding"
                ),
                EmbeddingModelSpec(
                    "voyage-multimodal-3",
                    "multimodal",
                    1024,
                    "medium",
                    "voyage",
                    "Voyage multimodal embedding",
                ),
                EmbeddingModelSpec(
                    "fake-embedding", "text", 10, "free", "fake", "Fake embedding for testing"
                ),
            ]
        )
