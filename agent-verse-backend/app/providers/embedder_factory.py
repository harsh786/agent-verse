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

Precedence (the same shape as the reasoning roles': explicit operator order >
env > configured registry model; see :mod:`app.providers.registry_embedder`):

1. the Model Registry's saved embedding preference order: its first eligible
   model, on its own ``base_url`` when it names one (OpenAI-compatible
   ``/v1/embeddings``), else on its provider, with failover only between
   endpoints of that SAME model id, refused (env order applies) when its width
   differs from ``EMBEDDING_DIM``;
2. the env order below (``EMBEDDING_BASE_URL`` / NVIDIA / on-prem, Voyage,
   OpenAI, Gemini, sentence-transformers);
3. when the env configures no embedder: the first operator-added registry
   embedding model with its own ``base_url``.

:class:`RegistryReloadingEmbedder` keeps a long-lived process (the API) on the
current choice: when the shared registry changes it re-resolves, so queries are
embedded with the same model the workers ingest with.
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
    "RegistryReloadingEmbedder",
    "apply_embedding_endpoint_settings",
    "build_query_embedder",
    "embedder_dimension",
    "embedder_model_name",
    "resolve_embedder",
    "target_embedding_dim",
]


@dataclass
class EmbedderResolution:
    """Outcome of embedder selection: the embedder, or why there is none."""

    embedder: Any = None
    # dedicated | voyage | openai | gemini | sentence_transformers, or — when the
    # registry preference order chose the model — the primary endpoint's provider.
    provider: str = ""
    model: str = ""
    dimension: int | None = None
    # (provider, reason) for every CONFIGURED provider that failed to build.
    errors: list[tuple[str, str]] = field(default_factory=list)
    # "registry" (the saved embedding preference order) or "env".
    source: str = "env"
    # Endpoints serving the registry-chosen model, in failover order.
    endpoints: list[str] = field(default_factory=list)
    # Why the preferred registry model was not used (the env order applied).
    registry_refusal: str = ""

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


def _endpoint_embed_model(base_url: str, fallback: str = "text-embedding-3-small") -> str:
    """The embedding model for a dedicated endpoint: a registry model of that
    endpoint's provider when it can be told from the URL, else the configured one
    (another provider's model would fail on this endpoint)."""
    from app.ai_router.model_catalog import provider_for_endpoint_url
    from app.ai_router.selection import resolve_embed_model
    from app.providers.model_defaults import configured_embed_model

    provider = provider_for_endpoint_url(base_url)
    if provider is None:
        return configured_embed_model(fallback)
    return resolve_embed_model(fallback, provider=provider)


def resolve_embedder(
    settings: Any = None, *, wire_registry_store: bool = False
) -> EmbedderResolution:
    """Select the embedding provider; record (and log loudly) every failure.

    The Model Registry's saved embedding preference order wins when its first
    eligible model is servable (see :mod:`app.providers.registry_embedder`).
    Otherwise — and always without a saved order — the env priority applies: a
    dedicated OpenAI-compatible ``EMBEDDING_BASE_URL`` endpoint (including NVIDIA
    / on-prem, see :func:`apply_embedding_endpoint_settings`), then Voyage,
    OpenAI, Gemini, and finally a local sentence-transformers model. Every
    configured provider is tried in turn until one builds.

    ``wire_registry_store``: a Celery worker passes True so the shared (Redis)
    registry store — where the preference order lives — is bound before
    selection, and the worker embeds with the SAME model as the API.
    """
    from app.ai_router.selection import resolve_embed_model
    from app.core.config import get_provider_env

    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()

    apply_embedding_endpoint_settings(settings)

    if wire_registry_store:
        _wire_worker_registry_store()
    registry_resolution = _resolve_from_registry(settings)
    if registry_resolution is not None and registry_resolution.embedder is not None:
        return registry_resolution
    registry_refusal = registry_resolution.registry_refusal if registry_resolution else ""
    registry_errors = list(registry_resolution.errors) if registry_resolution else []

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

            model = embed_model or _endpoint_embed_model(embed_base_url)
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
                default_model=resolve_embed_model("text-embedding-3-small", provider="openai"),
                embed_model=resolve_embed_model("text-embedding-3-small", provider="openai"),
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

    resolution = EmbedderResolution(errors=registry_errors, registry_refusal=registry_refusal)
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

    # Step 3: nothing in env, but the operator registered an embedding model
    # with its own endpoint.
    fallback = _resolve_registry_endpoint_fallback(settings)
    if fallback is not None:
        fallback.errors = [*resolution.errors, *fallback.errors]
        if fallback.embedder is not None:
            return fallback
        resolution.errors = fallback.errors
        resolution.registry_refusal = resolution.registry_refusal or fallback.registry_refusal

    if resolution.errors:
        logger.error(
            "embedder_unavailable",
            failed_providers=[p for p, _ in resolution.errors],
            reason=resolution.reason(),
        )
    return resolution


def target_embedding_dim(settings: Any) -> int | None:
    """The vector index width (``EMBEDDING_DIM``) after the NVIDIA / on-prem
    endpoint settings are applied, or ``None`` when it is not set."""
    apply_embedding_endpoint_settings(settings)
    try:
        dim = int(getattr(settings, "embedding_dim", 0) or 0)
    except (TypeError, ValueError):
        return None
    return dim if dim > 0 else None


def _resolve_from_registry(settings: Any) -> EmbedderResolution | None:
    """The registry-preferred embedder, a refusal, or ``None`` (no saved order).

    Never raises: a registry failure leaves the env order in charge.
    """
    from app.providers.registry_embedder import select_registry_embedder

    return _registry_resolution(select_registry_embedder, settings)


def _resolve_registry_endpoint_fallback(settings: Any) -> EmbedderResolution | None:
    """Precedence step 3 (no env embedder): a registry model with its own endpoint."""
    from app.providers.registry_embedder import select_registry_endpoint_embedder

    return _registry_resolution(select_registry_endpoint_embedder, settings)


def _registry_resolution(select: Callable[..., Any], settings: Any) -> EmbedderResolution | None:
    try:
        choice = select(settings, target_dim=target_embedding_dim(settings))
    except Exception as exc:
        logger.error("embedder_registry_selection_failed", error=f"{type(exc).__name__}: {exc}")
        return None
    if choice is None:
        return None
    resolution = EmbedderResolution(
        errors=list(choice.errors),
        source="registry" if choice.embedder is not None else "env",
        registry_refusal=choice.refusal,
    )
    if choice.embedder is None:
        return resolution
    resolution.embedder = choice.embedder
    resolution.provider = choice.provider
    resolution.model = choice.model_id
    resolution.endpoints = list(choice.endpoints)
    resolution.dimension = embedder_dimension(choice.embedder) or choice.dimension
    logger.info(
        "embedder_resolved",
        provider=resolution.provider,
        model=resolution.model,
        dimension=resolution.dimension,
        source="registry",
        endpoints=resolution.endpoints,
    )
    return resolution


_RELOAD_CHECK_INTERVAL_S = 5.0


def _registry_version() -> int | None:
    from app.ai_router.registry_store import get_model_registry_store

    store = get_model_registry_store()
    return store.version() if store is not None else None


class RegistryReloadingEmbedder:
    """The process embedder, re-resolved when the shared Model Registry changes.

    The API resolves its embedder once at startup, but an operator can save an
    embedding model / preference order at any time, on any replica; Celery
    workers resolve per task and pick it up at once. Without this the API kept
    embedding QUERIES with the old model while workers embedded DOCUMENTS with
    the new one: vectors of two models in one index, silently. Every embed
    checks the registry store's override version (at most every
    ``check_interval_s``) and, when it changed, re-runs ``resolve`` and swaps to
    the new embedder (``on_change`` callbacks then update the dependants, e.g.
    ``app.state.embedder_resolution``). A re-resolution that yields no embedder
    keeps the current one (logged). Everything else is delegated.
    """

    _agentverse_embedder_proxy = True

    def __init__(
        self,
        resolution: EmbedderResolution,
        *,
        resolve: Callable[[], EmbedderResolution],
        check_interval_s: float = _RELOAD_CHECK_INTERVAL_S,
        version: Callable[[], int | None] = _registry_version,
        clock: Callable[[], float] | None = None,
    ) -> None:
        import time

        if resolution.embedder is None:
            raise ValueError("RegistryReloadingEmbedder needs a resolved embedder")
        self._resolution = resolution
        self._resolve = resolve
        self._interval = check_interval_s
        self._version_fn = version
        self._clock = clock or time.monotonic
        self._version = self._read_version()
        self._checked_at = self._clock()
        self._on_change: list[Callable[[EmbedderResolution], None]] = []

    @property
    def resolution(self) -> EmbedderResolution:
        return self._resolution

    @property
    def current(self) -> Any:
        """The embedder in use right now (no version check)."""
        return self._resolution.embedder

    def add_change_listener(self, listener: Callable[[EmbedderResolution], None]) -> None:
        self._on_change.append(listener)

    def _read_version(self) -> int | None:
        try:
            return self._version_fn()
        except Exception as exc:  # never fail an embed on the version check
            logger.warning("embedder_registry_version_unreadable", error=str(exc)[:200])
            return None

    def refresh(self, *, force: bool = False) -> bool:
        """Re-resolve when the registry changed; True when the embedder changed."""
        now = self._clock()
        if not force and now - self._checked_at < self._interval:
            return False
        self._checked_at = now
        version = self._read_version()
        if not force and (version is None or version == self._version):
            return False
        self._version = version
        try:
            fresh = self._resolve()
        except Exception as exc:  # pragma: no cover - resolve_embedder never raises
            logger.error("embedder_registry_reload_failed", error=str(exc)[:200])
            return False
        if fresh.embedder is None:
            logger.error(
                "embedder_registry_reload_kept_current",
                model=self._resolution.model,
                reason=fresh.reason() or fresh.registry_refusal,
            )
            # Keep serving, but surface why the registry choice was not taken.
            self._resolution.registry_refusal = fresh.registry_refusal
            return False
        same = (
            fresh.source == self._resolution.source
            and fresh.provider == self._resolution.provider
            and fresh.model == self._resolution.model
            and fresh.endpoints == self._resolution.endpoints
            and fresh.dimension == self._resolution.dimension
        )
        if same:
            self._resolution.registry_refusal = fresh.registry_refusal
            return False
        logger.info(
            "embedder_registry_reloaded",
            previous_model=self._resolution.model,
            model=fresh.model,
            source=fresh.source,
            dimension=fresh.dimension,
        )
        self._resolution = fresh
        for listener in list(self._on_change):
            try:
                listener(fresh)
            except Exception as exc:
                logger.warning("embedder_reload_listener_failed", error=str(exc)[:200])
        return True

    async def embed(self, request: Any) -> Any:
        self.refresh()
        return await self._resolution.embedder.embed(request)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.refresh()
        inner = self._resolution.embedder
        batch = getattr(inner, "embed_batch", None)
        if callable(batch):
            vectors: list[list[float]] = await batch(texts)
            return vectors
        from app.providers.base import EmbedRequest

        resp = await inner.embed(EmbedRequest(texts=texts))
        return list(resp.embeddings)

    def __getattr__(self, name: str) -> Any:
        if name == "_resolution":  # pragma: no cover - only while unpickling
            raise AttributeError(name)
        return getattr(self._resolution.embedder, name)


def _wire_worker_registry_store() -> None:
    """Bind the shared model-registry store in a worker process (best effort)."""
    try:
        import app.scaling.tasks as _tasks

        _tasks._wire_worker_model_registry_store()
    except Exception as exc:  # never block embedding on the registry
        logger.warning("embedder_registry_store_wire_failed", error=str(exc)[:200])


def build_query_embedder(settings: Any = None, *, wire_registry_store: bool = False) -> Any:
    """Return the configured embedding provider, or ``None`` when none is usable."""
    return resolve_embedder(settings, wire_registry_store=wire_registry_store).embedder


def embedder_model_name(embedder: Any) -> str:
    """Best-effort name of the model an embedding provider produces vectors with."""
    if embedder is None:
        return ""
    from app.observability.traced_provider import unwrap_provider

    embedder = unwrap_provider(embedder)
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
