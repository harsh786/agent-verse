"""Embedding Router - vendor-agnostic embedding with fallbacks."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

_log = logging.getLogger(__name__)


@dataclass
class EmbeddingConfig:
    """Configuration for an embedding provider/model."""

    provider: str
    model: str
    dimension: int
    max_batch_size: int = 100
    max_tokens: int = 8192
    cost_per_1k: float = 0.0


BUILTIN_EMBEDDING_CONFIGS = {
    "openai/text-embedding-3-large": EmbeddingConfig(
        provider="openai", model="text-embedding-3-large", dimension=3072, cost_per_1k=0.00013
    ),
    "openai/text-embedding-3-small": EmbeddingConfig(
        provider="openai", model="text-embedding-3-small", dimension=1536, cost_per_1k=0.00002
    ),
    "voyage/voyage-3-large": EmbeddingConfig(
        provider="voyage", model="voyage-3-large", dimension=1024, cost_per_1k=0.00018
    ),
    "voyage/voyage-3-lite": EmbeddingConfig(
        provider="voyage", model="voyage-3-lite", dimension=512, cost_per_1k=0.000016
    ),
    "gemini/text-embedding-004": EmbeddingConfig(
        provider="gemini", model="text-embedding-004", dimension=768, cost_per_1k=0.00000
    ),
}


class EmbeddingRouter:
    """Route embedding requests to the correct provider with fallback."""

    def __init__(self, provider: Any = None) -> None:
        self._provider: Any = provider  # can be injected at construction or via set_provider()
        self._configs = dict(BUILTIN_EMBEDDING_CONFIGS)
        self._usage: dict[str, int] = {}  # model → total tokens embedded
        self._errors: dict[str, int] = {}  # model → error count

    def set_provider(self, provider: Any) -> None:
        self._provider = provider

    def get_config(self, provider: str, model: str) -> EmbeddingConfig | None:
        return self._configs.get(f"{provider}/{model}")

    def validate_dimension(self, collection_dimension: int, model_dimension: int) -> bool:
        """Validate that the embedding dimension matches the collection's configured dimension."""
        return collection_dimension == model_dimension

    async def embed_texts(
        self,
        texts: list[str],
        provider: str = "openai",
        model: str = "text-embedding-3-small",
        fallback_lexical: bool = True,
    ) -> list[list[float]]:
        """Embed texts using the specified provider with lexical fallback."""
        if not texts:
            return []

        model_key = f"{provider}/{model}"
        if self._provider is not None:
            try:
                # C7 fix: use embed(EmbedRequest) — the required Protocol method.
                # embed_batch() is an optional default that some provider ducks may not have.
                from app.providers.base import EmbedRequest

                resp = await self._provider.embed(EmbedRequest(texts=texts))
                embeddings = resp.embeddings if resp.embeddings else []
                token_count = sum(len(t.split()) for t in texts)
                self._usage[model_key] = self._usage.get(model_key, 0) + token_count
                return embeddings
            except Exception as exc:
                self._errors[model_key] = self._errors.get(model_key, 0) + 1
                _log.warning("Embedding via provider failed: %s", exc)

        if fallback_lexical:
            # Deterministic fallback — BM25-style sparse vector simulation
            return [self._lexical_embed(t) for t in texts]

        raise RuntimeError("Embedding failed and lexical fallback is disabled")

    def _lexical_embed(self, text: str, dim: int = 384) -> list[float]:
        """Deterministic fallback embedding using character hashing."""
        vec = [0.0] * dim
        words = text.lower().split()
        for word in words:
            idx = hash(word) % dim
            vec[idx] += 1.0
        # Normalize
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def get_usage_stats(self) -> dict[str, Any]:
        return {"usage_by_model": self._usage, "errors_by_model": self._errors}

    def get_drift_metrics(self) -> dict[str, Any]:
        """Return embedding usage and error metrics for drift monitoring."""
        total = sum(self._usage.values())
        errors = sum(self._errors.values())
        return {
            "total_tokens_embedded": total,
            "total_errors": errors,
            "error_rate": errors / max(total + errors, 1),
            "models_used": list(self._usage.keys()),
            "usage_by_model": dict(self._usage),
            "errors_by_model": dict(self._errors),
        }


# Module-level singleton
embedding_router = EmbeddingRouter()
