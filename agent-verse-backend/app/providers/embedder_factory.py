"""The ONE embedding provider used for both document and query vectors.

Retrieval embeds queries with ``app.state.embedder``; the ingestion pipeline
used to embed documents with the *chat* LLM provider (``_app_provider`` in the
API, ``resolve_provider()`` in the Celery worker). Those are different models —
often different dimensions — so document and query vectors lived in different
spaces and similarity scores were noise: retrieval quality silently broken,
with nothing failing.

:func:`resolve_embedder` is the ONE selection every process uses (the API's
``create_app`` and the Celery worker alike), so both build the exact same
embedder from the same configuration. It reads typed :class:`Settings` first
(which include ``.env``) and ``os.environ`` as a fallback, applies the NVIDIA /
on-prem embedding endpoint itself (:func:`apply_embedding_endpoint_settings` —
this used to happen only inside ``create_app``, so the worker had no embedder
on NVIDIA-only config), tries every configured provider in priority order (a
provider that fails no longer silently skips the rest) and logs each failure at
error level. :func:`embedder_model_name` names the model so each chunk can record
the ``embedding_model`` that produced its vector (LAW-08).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "EmbedderResolution",
    "apply_embedding_endpoint_settings",
    "build_query_embedder",
    "embedder_dimension",
    "embedder_model_name",
    "resolve_embedder",
]


@dataclass
class EmbedderResolution:
    """Outcome of embedder selection: the embedder, or why there is none."""

    embedder: Any = None
    provider: str = ""  # dedicated | voyage | openai | gemini | sentence_transformers
    model: str = ""
    dimension: int | None = None
    # (provider, reason) for every CONFIGURED provider that failed to build.
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.embedder is not None:
            return "available"
        return "unavailable" if self.errors else "not_configured"

    def reason(self) -> str:
        """Human-readable reason there is no embedder (for authenticated 503s)."""
        if self.embedder is not None:
            return ""
        if not self.errors:
            return (
                "embedding provider not configured (set EMBEDDING_BASE_URL, "
                "VOYAGE_API_KEY, OPENAI_API_KEY, GOOGLE_API_KEY, NVIDIA_EMBED_MODEL or "
                "SENTENCE_TRANSFORMERS_MODEL)"
            )
        return "; ".join(f"{p} failed to load ({r})" for p, r in self.errors)

    def public_summary(self) -> dict[str, Any]:
        """Status for the unauthenticated /health route — never error text."""
        return {
            "status": self.status,
            "provider": self.provider or None,
            "model": self.model or None,
            "dimension": self.dimension,
            "failed_providers": [p for p, _ in self.errors],
        }


def _setting(settings: Any, attr: str, env: str, default: str = "") -> str:
    """Typed setting first (it includes ``.env``), then the raw process env."""
    value = getattr(settings, attr, "") or os.getenv(env, "") or default
    return str(value)


def apply_embedding_endpoint_settings(settings: Any) -> None:
    """Point the dedicated embedding endpoint at NVIDIA / the on-prem cluster.

    NVIDIA's embedding model wins when keyed and set (its dim must match the DB),
    else the on-prem embedding endpoint. Only fills when no embedding endpoint is
    explicitly configured (in Settings or the process env). Idempotent. Shared by
    ``create_app`` and :func:`resolve_embedder`, so the Celery worker — which never
    runs ``create_app`` — resolves the same embedder as the API.
    """
    if getattr(settings, "embedding_base_url", "") or os.getenv("EMBEDDING_BASE_URL", ""):
        return
    nvidia_on = bool(str(getattr(settings, "nvidia_api_key", "") or "").strip())
    onprem_on = bool(
        getattr(settings, "onprem_enabled", False)
        and str(getattr(settings, "onprem_qwen_base_url", "") or "").strip()
    )
    nvidia_model = str(getattr(settings, "nvidia_embed_model", "") or "").strip()
    try:
        if nvidia_on and nvidia_model:
            settings.embedding_base_url = settings.nvidia_base_url
            settings.embedding_model = nvidia_model
            settings.embedding_api_key = settings.nvidia_api_key
            settings.embedding_dim = settings.nvidia_embed_dim
        elif onprem_on and bool(getattr(settings, "onprem_embedding_base_url", None)):
            settings.embedding_base_url = settings.onprem_embedding_base_url
            settings.embedding_model = settings.embedding_model or settings.onprem_embedding_model
            settings.embedding_api_key = settings.embedding_api_key or settings.onprem_api_key
            settings.embedding_dim = settings.onprem_embedding_dim
    except (AttributeError, TypeError, ValueError) as exc:  # frozen/duck-typed settings
        logger.error("embedding_endpoint_settings_failed", error=str(exc))


def _declared_endpoint_dim(settings: Any) -> int | None:
    """Model-specific dim of an NVIDIA / on-prem embedding endpoint, else None.

    Those deployments declare their model's width (``nvidia_embed_dim`` /
    ``onprem_embedding_dim``); a generic ``EMBEDDING_BASE_URL`` has only the
    static ``embedding_dim`` default, which is not evidence of the real width.
    """
    base_url = getattr(settings, "embedding_base_url", "")
    if not base_url:
        return None
    if base_url == getattr(settings, "nvidia_base_url", None) and getattr(
        settings, "embedding_model", ""
    ) == getattr(settings, "nvidia_embed_model", None):
        return int(settings.nvidia_embed_dim)
    if base_url == getattr(settings, "onprem_embedding_base_url", None):
        return int(settings.onprem_embedding_dim)
    return None


def resolve_embedder(settings: Any = None) -> EmbedderResolution:
    """Select the embedding provider; record (and log loudly) every failure.

    Priority: a dedicated OpenAI-compatible ``EMBEDDING_BASE_URL`` endpoint
    (including NVIDIA / on-prem, see :func:`apply_embedding_endpoint_settings`),
    then Voyage, OpenAI, Gemini, and finally a local sentence-transformers model.
    Every configured provider is tried in turn until one builds.
    """
    from app.ai_router.selection import resolve_embed_model
    from app.core.config import get_provider_env

    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()

    apply_embedding_endpoint_settings(settings)

    def _key(attr: str, env: str) -> str:
        return str(getattr(settings, attr, "") or "") or get_provider_env(env)

    candidates: list[tuple[str, Callable[[], Any], int | None]] = []

    # Highest priority: a dedicated OpenAI-compatible embedding endpoint (its own
    # base_url + model), e.g. a self-hosted Qwen3-Embedding on vLLM or NVIDIA NIM.
    embed_base_url = _setting(settings, "embedding_base_url", "EMBEDDING_BASE_URL")
    if embed_base_url:
        embed_model = _setting(settings, "embedding_model", "EMBEDDING_MODEL")
        embed_key = _setting(settings, "embedding_api_key", "EMBEDDING_API_KEY", "sk-noauth")

        def _dedicated() -> Any:
            from app.providers.openai_compatible import OpenAICompatibleProvider

            model = embed_model or resolve_embed_model("text-embedding-3-small")
            return OpenAICompatibleProvider(
                api_key=embed_key, base_url=embed_base_url, default_model=model, embed_model=model
            )

        candidates.append(("dedicated", _dedicated, _declared_endpoint_dim(settings)))

    voyage_key = _key("voyage_api_key", "VOYAGE_API_KEY")
    if voyage_key:

        def _voyage() -> Any:
            from app.providers.voyage_provider import VoyageProvider

            return VoyageProvider(api_key=voyage_key)

        candidates.append(("voyage", _voyage, None))

    openai_key = _key("openai_api_key", "OPENAI_API_KEY")
    if openai_key:

        def _openai() -> Any:
            from app.providers.openai_compatible import OpenAICompatibleProvider

            return OpenAICompatibleProvider(
                api_key=openai_key,
                base_url=os.getenv("OPENAI_BASE_URL", ""),
                default_model=resolve_embed_model("text-embedding-3-small"),
                embed_model=resolve_embed_model("text-embedding-3-small"),
            )

        candidates.append(("openai", _openai, None))

    google_key = _key("google_api_key", "GOOGLE_API_KEY")
    if google_key:

        def _gemini() -> Any:
            from app.providers.gemini_provider import GeminiProvider

            return GeminiProvider(api_key=google_key)

        candidates.append(("gemini", _gemini, None))

    st_model = _setting(settings, "sentence_transformers_model", "SENTENCE_TRANSFORMERS_MODEL")
    if st_model:

        def _local() -> Any:
            from app.providers.voyage_provider import LocalEmbedProvider

            return LocalEmbedProvider(model_name=st_model)

        candidates.append(("sentence_transformers", _local, None))

    resolution = EmbedderResolution()
    for name, build, declared_dim in candidates:
        try:
            embedder = build()
        except Exception as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:300]}"
            resolution.errors.append((name, reason))
            logger.error("embedder_provider_failed", provider=name, reason=reason)
            continue
        resolution.embedder = embedder
        resolution.provider = name
        resolution.model = embedder_model_name(embedder)
        resolution.dimension = embedder_dimension(embedder) or (
            int(declared_dim) if declared_dim else None
        )
        logger.info(
            "embedder_resolved",
            provider=name,
            model=resolution.model,
            dimension=resolution.dimension,
            skipped_failed=[p for p, _ in resolution.errors],
        )
        return resolution

    if resolution.errors:
        logger.error(
            "embedder_unavailable",
            failed_providers=[p for p, _ in resolution.errors],
            reason=resolution.reason(),
        )
    return resolution


def build_query_embedder(settings: Any = None) -> Any:
    """Return the configured embedding provider, or ``None`` when none is usable."""
    return resolve_embedder(settings).embedder


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


def embedder_dimension(embedder: Any) -> int | None:
    """The embedder's REAL output dimension when it is known locally, else None."""
    if embedder is None:
        return None
    for attr in ("embedding_dim", "embedding_dimension", "dimension", "_embed_dim"):
        value = getattr(embedder, attr, None)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None
