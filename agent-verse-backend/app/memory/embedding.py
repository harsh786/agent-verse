"""Adapter from the app's embedding provider to the canonical memory embedder.

``memory_records.embedding`` is a fixed ``vector(2048)`` column behind a
``halfvec(2048)`` HNSW index (MEM-38: it was 1536, so the default 2048-d
embedder never produced a vector). Deployments embed with whatever provider is
configured — 1024-d Voyage/Qwen, 1536-d OpenAI small, 2048-d NVIDIA, 3072-d
OpenAI large — so the adapter fits vectors the same way long-term memory does:

* exactly 2048-d → stored as is;
* narrower → zero-padded (padding changes neither dot products nor norms, so
  cosine similarity ranks exactly as in a column of the native width);
* wider → cannot be shrunk without changing its geometry, so no vector is
  produced and the record is recalled lexically.

The adapter carries a ``model_id`` that the repository stores with each vector
and filters on at recall time, so vectors from different models are never
compared. Provider failures degrade to lexical-only memory and are logged.
"""

from __future__ import annotations

from typing import Any

from app.observability.logging import get_logger

MEMORY_EMBEDDING_DIM = 2048

_log = get_logger(__name__)


def fit_memory_vector(vec: list[float] | tuple[float, ...]) -> tuple[float, ...] | None:
    n = len(vec)
    if n == MEMORY_EMBEDDING_DIM:
        return tuple(float(v) for v in vec)
    if 0 < n < MEMORY_EMBEDDING_DIM:
        return (*(float(v) for v in vec), *([0.0] * (MEMORY_EMBEDDING_DIM - n)))
    return None


def _provider_model_id(provider: Any) -> str:
    """The configured embedding MODEL name (MEM-10).

    It used to be ``<wrapper class>:<model>``, so the same model behind a
    different wrapper (traced, routed, BYOK) never matched at recall time. The
    class name is used only when the provider exposes no model name.
    """
    from app.providers.embedder_factory import embedder_model_name

    name = embedder_model_name(provider)
    if not name or name == type(provider).__name__:
        for attr in ("embedding_model", "default_model", "model"):
            value = getattr(provider, attr, None)
            if isinstance(value, str) and value:
                name = value
                break
    return (name or type(provider).__name__)[:128]


class ProviderMemoryEmbedder:
    """Callable ``(text) -> 2048-d tuple | None`` over an ``LLMProvider.embed``."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self.model_id = _provider_model_id(provider)

    async def __call__(self, text: str) -> tuple[float, ...] | None:
        vector, _reason = await self.embed_checked(text)
        return vector

    async def embed_checked(self, text: str) -> tuple[tuple[float, ...] | None, str]:
        """``(vector, reason)``: reason is ``ok``, ``too_wide`` (permanent for
        this model — the sweep marks the record), ``empty`` or ``failed``."""
        from app.providers.base import EmbedRequest

        try:
            response = await self._provider.embed(EmbedRequest(texts=[text]))
        except Exception as exc:
            _log.warning(
                "memory_embedding_failed", model=self.model_id, error=str(exc)[:200]
            )
            return None, "failed"
        embeddings = getattr(response, "embeddings", None) or []
        if not embeddings:
            _log.warning("memory_embedding_empty", model=self.model_id)
            return None, "empty"
        fitted = fit_memory_vector(embeddings[0])
        if fitted is None:
            _log.info(
                "memory_embedding_too_wide",
                model=self.model_id,
                dimension=len(embeddings[0]),
            )
            return None, "too_wide"
        return fitted, "ok"


def memory_embedder_from_provider(provider: Any) -> ProviderMemoryEmbedder | None:
    """Wrap the app embedder, or None when there is no usable embedding provider."""
    if provider is None or not callable(getattr(provider, "embed", None)):
        return None
    return ProviderMemoryEmbedder(provider)


__all__ = [
    "MEMORY_EMBEDDING_DIM",
    "ProviderMemoryEmbedder",
    "fit_memory_vector",
    "memory_embedder_from_provider",
]
