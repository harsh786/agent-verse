"""The ONE embedding provider used for both document and query vectors.

Retrieval embeds queries with ``app.state.embedder``; the ingestion pipeline
used to embed documents with the *chat* LLM provider (``_app_provider`` in the
API, ``resolve_provider()`` in the Celery worker). Those are different models —
often different dimensions — so document and query vectors lived in different
spaces and similarity scores were noise: retrieval quality silently broken,
with nothing failing.

:func:`build_query_embedder` is the selection ``create_app`` always used for
``app.state.embedder`` (moved here verbatim so the Celery worker builds the
exact same embedder instead of re-implementing — or skipping — the priority
order). :func:`embedder_model_name` names the model so each chunk can record the
``embedding_model`` that produced its vector (LAW-08).
"""

from __future__ import annotations

import os
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = ["build_query_embedder", "embedder_model_name"]


def build_query_embedder(settings: Any = None) -> Any:
    """Return the configured embedding provider, or ``None`` when none is set.

    Priority (unchanged from ``create_app``): a dedicated OpenAI-compatible
    ``EMBEDDING_BASE_URL`` endpoint, then Voyage, OpenAI, Gemini, and finally a
    local sentence-transformers model.
    """
    from app.ai_router.selection import resolve_embed_model
    from app.core.config import get_provider_env

    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()

    embedder: Any = None
    openai_key = get_provider_env("OPENAI_API_KEY")
    voyage_key = get_provider_env("VOYAGE_API_KEY")
    # Highest priority: a dedicated OpenAI-compatible embedding endpoint (its own
    # base_url + model), e.g. a self-hosted Qwen3-Embedding on vLLM. This is
    # separate from the chat LLM base_url so reasoning and embedding can live on
    # different servers.
    embed_base_url = os.getenv("EMBEDDING_BASE_URL", "") or getattr(
        settings, "embedding_base_url", ""
    )
    if embed_base_url:
        try:
            from app.providers.openai_compatible import OpenAICompatibleProvider

            embed_model = os.getenv("EMBEDDING_MODEL", "") or getattr(
                settings, "embedding_model", ""
            )
            embedder = OpenAICompatibleProvider(
                api_key=(
                    os.getenv("EMBEDDING_API_KEY", "")
                    or getattr(settings, "embedding_api_key", "")
                    or "sk-noauth"
                ),
                base_url=embed_base_url,
                default_model=embed_model or resolve_embed_model("text-embedding-3-small"),
                embed_model=embed_model or resolve_embed_model("text-embedding-3-small"),
            )
            logger.info(
                "dedicated_embed_provider_wired", base_url=embed_base_url, model=embed_model
            )
        except Exception as exc:
            logger.warning("dedicated_embed_provider_failed", error=str(exc))
    if voyage_key and not embedder:
        try:
            from app.providers.voyage_provider import VoyageProvider

            embedder = VoyageProvider(api_key=voyage_key)
        except Exception:
            pass
    elif openai_key and not embedder:
        try:
            from app.providers.openai_compatible import OpenAICompatibleProvider

            embedder = OpenAICompatibleProvider(
                api_key=openai_key,
                base_url=os.getenv("OPENAI_BASE_URL", ""),
                default_model=resolve_embed_model("text-embedding-3-small"),
                embed_model=resolve_embed_model("text-embedding-3-small"),
            )
        except Exception:
            pass
    elif get_provider_env("GOOGLE_API_KEY") and not embedder:
        try:
            from app.providers.gemini_provider import GeminiProvider

            embedder = GeminiProvider(api_key=get_provider_env("GOOGLE_API_KEY"))
        except Exception:
            pass
    elif os.getenv("SENTENCE_TRANSFORMERS_MODEL", "") and not embedder:
        try:
            from app.providers.voyage_provider import LocalEmbedProvider

            embedder = LocalEmbedProvider(
                model_name=os.getenv("SENTENCE_TRANSFORMERS_MODEL", "all-MiniLM-L6-v2")
            )
            logger.info(
                "local_embed_provider_wired",
                model=os.getenv("SENTENCE_TRANSFORMERS_MODEL"),
            )
        except Exception as exc:
            logger.warning("local_embed_provider_failed", error=str(exc))
    return embedder


def embedder_model_name(embedder: Any) -> str:
    """Best-effort name of the model an embedding provider produces vectors with."""
    if embedder is None:
        return ""
    for attr in ("_embed_model_name", "embed_model_name", "_embed_model", "embed_model",
                 "_model_name", "model_name", "_model"):
        value = getattr(embedder, attr, None)
        if isinstance(value, str) and value:
            return value
    return type(embedder).__name__
