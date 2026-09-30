"""Embedding Router - vendor-agnostic embedding; lexical fallback only on explicit opt-in."""

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


LEXICAL_FALLBACK_MODEL = "lexical-hash-384"


class EmbeddingUnavailableError(RuntimeError):
    """No embedding provider is configured, or it failed, and no fallback was requested."""


class EmbeddingModelUnavailableError(EmbeddingUnavailableError):
    """The configured provider does not serve the requested embedding model."""

    def __init__(self, requested: str, served: str) -> None:
        self.requested = requested
        self.served = served
        super().__init__(
            f"embedding model {requested!r} is not served by the configured embedding "
            f"provider (it serves {served!r})"
        )


def _same_model(served: str, requested: str) -> bool:
    """``nvidia/nv-embedqa-e5-v5`` serves a request for ``nv-embedqa-e5-v5``."""
    a, b = served.strip().lower(), requested.strip().lower()
    return a == b or a.rsplit("/", 1)[-1] == b.rsplit("/", 1)[-1]


@dataclass
class EmbeddingRunResult:
    """What an embedding call actually produced (never ambiguous about fallback)."""

    embeddings: list[list[float]]
    model: str
    used_fallback: bool
    error: str = ""


class EmbeddingRouter:
    """Route embedding requests to the configured provider (fallback is opt-in)."""

    def __init__(self, provider: Any = None) -> None:
        self._provider: Any = provider  # can be injected at construction or via set_provider()
        self._configs = dict(BUILTIN_EMBEDDING_CONFIGS)
        self._usage: dict[str, int] = {}  # model → total tokens embedded
        self._errors: dict[str, int] = {}  # model → error count
        # Per-tenant counters for GET /embeddings/usage. The process-wide ones
        # above feed platform drift metrics only; returning them per request
        # showed every tenant every other tenant's embedding volume.
        self._tenant_usage: dict[str, dict[str, int]] = {}
        self._tenant_errors: dict[str, dict[str, int]] = {}

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
        provider: str = "",
        model: str = "",
        fallback_lexical: bool = False,
        tenant_id: str = "",
    ) -> list[list[float]]:
        """Embed *texts* on the configured provider.

        Raises ``EmbeddingUnavailableError`` when there is no provider or it
        fails, unless the caller explicitly opts into ``fallback_lexical`` (a
        deterministic hashing vector with NO semantic meaning). The default used
        to be ``True``, so any caller that forgot to opt out silently received
        fake 384-dim vectors as if they were real embeddings.
        """
        result = await self.embed_texts_report(
            texts,
            provider=provider,
            model=model,
            fallback_lexical=fallback_lexical,
            tenant_id=tenant_id,
        )
        return result.embeddings

    async def embed_texts_report(
        self,
        texts: list[str],
        *,
        provider: str = "",
        model: str = "",
        fallback_lexical: bool = False,
        provider_impl: Any = None,
        tenant_id: str = "",
    ) -> EmbeddingRunResult:
        """Like :meth:`embed_texts` but reports what actually produced the vectors.

        ``provider_impl`` overrides the router's provider for this call only
        (the API passes the app's provider per request instead of mutating this
        process-wide singleton).

        A requested ``model`` is forwarded to the provider, and a result served
        by a different model raises :class:`EmbeddingModelUnavailableError`
        (never silently the default model's vectors). Without one the
        provider's configured model is used.
        """
        model_key = f"{provider}/{model}" if model else (provider or "default")
        if not texts:
            return EmbeddingRunResult(embeddings=[], model=model_key, used_fallback=False)

        impl = provider_impl if provider_impl is not None else self._provider
        error = "no embedding provider configured"
        if impl is not None:
            try:
                # C7 fix: use embed(EmbedRequest) — the required Protocol method.
                # embed_batch() is an optional default that some provider ducks may not have.
                from app.providers.base import EmbedRequest

                resp = await impl.embed(EmbedRequest(texts=texts, model=model))
                if model:
                    from app.providers.embedder_factory import embedder_model_name

                    served = str(getattr(resp, "model", "") or "") or embedder_model_name(impl)
                    if not _same_model(served, model):
                        raise EmbeddingModelUnavailableError(model, served)
                embeddings = list(resp.embeddings or [])
                if len(embeddings) != len(texts) or any(not e for e in embeddings):
                    raise RuntimeError(
                        f"provider returned {len(embeddings)} usable vectors for {len(texts)} texts"
                    )
                token_count = sum(len(t.split()) for t in texts)
                self._usage[model_key] = self._usage.get(model_key, 0) + token_count
                if tenant_id:
                    t_usage = self._tenant_usage.setdefault(tenant_id, {})
                    t_usage[model_key] = t_usage.get(model_key, 0) + token_count
                actual = str(getattr(resp, "model", "") or "") or model_key
                if tenant_id:
                    from app.embedding.usage import record_embedding_usage

                    await record_embedding_usage(tenant_id, actual, token_count)
                return EmbeddingRunResult(embeddings=embeddings, model=actual, used_fallback=False)
            except EmbeddingModelUnavailableError:
                raise  # a client error: no fallback substitutes another model
            except Exception as exc:
                self._errors[model_key] = self._errors.get(model_key, 0) + 1
                if tenant_id:
                    t_errors = self._tenant_errors.setdefault(tenant_id, {})
                    t_errors[model_key] = t_errors.get(model_key, 0) + 1
                error = f"{type(exc).__name__}: {exc}"[:300]
                _log.warning("Embedding via provider failed: %s", exc)

        if fallback_lexical:
            # Explicit opt-in only. Deterministic hashing vectors — NOT semantic.
            return EmbeddingRunResult(
                embeddings=[self._lexical_embed(t) for t in texts],
                model=LEXICAL_FALLBACK_MODEL,
                used_fallback=True,
                error=error,
            )

        raise EmbeddingUnavailableError(error)

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

    def get_usage_stats(self, *, tenant_id: str) -> dict[str, Any]:
        """One tenant's embedding usage/error counters (this process)."""
        return {
            "usage_by_model": dict(self._tenant_usage.get(tenant_id, {})),
            "errors_by_model": dict(self._tenant_errors.get(tenant_id, {})),
        }

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
