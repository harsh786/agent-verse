"""EmbeddingModelRegistry — catalogue of available embedding models."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EmbeddingModelSpec:
    model_id: str
    modality: str               # text|code|multimodal|image
    dimension: int
    cost_class: str             # free|low|medium|high
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
    def build_default(cls) -> "EmbeddingModelRegistry":
        return cls([
            EmbeddingModelSpec("text-embedding-3-small", "text", 1536, "low",
                               "openai", "OpenAI small text embedding"),
            EmbeddingModelSpec("text-embedding-3-large", "text", 3072, "medium",
                               "openai", "OpenAI large text embedding"),
            EmbeddingModelSpec("voyage-3-lite", "text", 512, "low",
                               "voyage", "Voyage text embedding lite"),
            EmbeddingModelSpec("voyage-code-3", "code", 1024, "low",
                               "voyage", "Voyage code embedding"),
            EmbeddingModelSpec("voyage-multimodal-3", "multimodal", 1024, "medium",
                               "voyage", "Voyage multimodal embedding"),
            EmbeddingModelSpec("fake-embedding", "text", 10, "free",
                               "fake", "Fake embedding for testing"),
        ])
