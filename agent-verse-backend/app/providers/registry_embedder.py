"""Registry-driven embedder selection, with failover between endpoints of ONE model.

When the operator has saved an embedding preference order
(``PUT /models/preferences/embedding``), :func:`select_registry_embedder` picks
the first eligible model of that order and builds its provider's embedder,
instead of the env-driven order in
:func:`app.providers.embedder_factory.resolve_embedder`. Without a saved order it
returns ``None`` and the env order applies exactly as before.

Vectors of different embedding models are NOT comparable, even at the same
width. So:

* The chosen model is refused (env order applies, with a logged reason) when its
  known output width differs from the vector index's ``EMBEDDING_DIM``.
* :class:`SameModelFailoverEmbedder` fails over only between endpoints that serve
  the SAME model id (e.g. a Qwen3-Embedding model on the on-prem vLLM and on
  another OpenAI-compatible endpoint). It never switches to another model: if
  every endpoint of the model fails, the call fails.

A registry entry with its OWN endpoint (``base_url``, e.g. a vLLM server at
``http://192.168.63.104:30082/v1``) is embedded THERE, over the OpenAI-compatible
``/v1/embeddings`` API, not on the provider's env endpoint, exactly like the
chat roles' per-model dispatch (``app.providers.model_dispatch``). The URL passes
the model-endpoint egress policy (``app.ai_router.model_endpoints``: private
networks per ``ALLOW_PRIVATE_NETWORK_ACCESS``; metadata / link-local / 0.0.0.0 /
multicast never) when the embedder is built AND at every connect (SSRF-pinned
client). Its credential is the one saved with the entry (vault-encrypted), else
the provider's env key.

Requested width: an entry may carry ``output_dimensions`` (models that can
shorten their vectors, e.g. OpenAI ``text-embedding-3-*`` or Gemini
``gemini-embedding-001``). It is sent as ``dimensions`` on every OpenAI-compatible
``/embeddings`` request (``output_dimensionality`` on the native Gemini API), so
such a model can be matched to the index width on purpose.

Dimension safety: a model's width is the one a probe MEASURED ("Test
connection" or first use, persisted with the registry entry), else the width it
is asked for (``output_dimensions``), else the catalog / declared width. A
known width that differs from the index's ``EMBEDDING_DIM`` is refused up front.
An unknown width is checked on the first response by
:class:`DimensionCheckedEmbedder`: a mismatch raises
:class:`app.rag.store.EmbeddingDimensionError` (no vector is ever returned to be
written), a match is recorded so the next selection knows it.

Selection precedence (consistent with the reasoning roles, see
``app.ai_router.role_preference``: explicit operator order > env > cheapest
configured):

1. the saved embedding preference order: its first eligible model, on its own
   ``base_url`` when it has one;
2. the env-configured embedder (``app.providers.embedder_factory``:
   ``EMBEDDING_BASE_URL`` / NVIDIA / on-prem, Voyage, OpenAI, Gemini,
   sentence-transformers);
3. only when the env configures no embedder at all: the first operator-added
   registry embedding model that names its own ``base_url``
   (:func:`select_registry_endpoint_embedder`).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from app.observability.logging import get_logger
from app.providers.base import (
    CompletionRequest,
    CompletionResponse,
    EmbedRequest,
    EmbedResponse,
)

logger = get_logger(__name__)

__all__ = [
    "DimensionCheckedEmbedder",
    "EndpointNotConfiguredError",
    "RegistryEmbedderChoice",
    "SameModelFailoverEmbedder",
    "build_endpoint_embedder",
    "build_registry_model_embedder",
    "embedding_dimension_status",
    "embedding_model_dimension",
    "record_embedding_dimension",
    "registry_output_dimensions",
    "select_registry_embedder",
    "select_registry_endpoint_embedder",
]

_T = TypeVar("_T")

_PROVIDER_ALIASES = {"google": "gemini"}
_DEFAULT_FAILOVER_TIMEOUT_S = 120.0
_DEFAULT_COOLDOWN_S = 30.0


class EndpointNotConfiguredError(ValueError):
    """This deployment has no endpoint / credentials for the provider."""


def _norm(provider: str) -> str:
    p = (provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(p, p)


def _setting(settings: Any, attr: str) -> str:
    return str(getattr(settings, attr, "") or "").strip()


def _secret(settings: Any, attr: str, env: str) -> str:
    """Typed setting first, then the provider env (process env / global settings)."""
    from app.core.config import get_provider_env

    return _setting(settings, attr) or get_provider_env(env)


# ── Endpoint construction ────────────────────────────────────────────────────


def build_endpoint_embedder(
    provider: str, model_id: str, settings: Any, *, dimensions: int | None = None
) -> Any:
    """An embedder producing *model_id* vectors on *provider*'s endpoint.

    *dimensions*: the output width to request (``dimensions`` on OpenAI-compatible
    endpoints, ``output_dimensionality`` on Gemini); ``None`` = native width.
    Raises :class:`EndpointNotConfiguredError` when this deployment has no
    credentials / endpoint for *provider* (not servable here).
    """
    p = _norm(provider)
    if p == "nvidia":
        key = _secret(settings, "nvidia_api_key", "NVIDIA_API_KEY")
        if not key:
            raise EndpointNotConfiguredError("NVIDIA_API_KEY is not set")
        base = _setting(settings, "nvidia_base_url") or "https://integrate.api.nvidia.com/v1"
        return _openai_compatible(key, base, model_id, dimensions=dimensions)
    if p == "onprem":
        base = _setting(settings, "onprem_embedding_base_url")
        if not (getattr(settings, "onprem_enabled", False) and base):
            raise EndpointNotConfiguredError(
                "no on-prem embedding endpoint (ONPREM_ENABLED + ONPREM_EMBEDDING_BASE_URL)"
            )
        return _openai_compatible(
            _setting(settings, "onprem_api_key") or "EMPTY", base, model_id, dimensions=dimensions
        )
    if p == "openai":
        key = _secret(settings, "openai_api_key", "OPENAI_API_KEY")
        if not key:
            raise EndpointNotConfiguredError("OPENAI_API_KEY is not set")
        return _openai_compatible(
            key, os.getenv("OPENAI_BASE_URL") or None, model_id, dimensions=dimensions
        )
    if p == "gemini":
        key = _secret(settings, "google_api_key", "GOOGLE_API_KEY")
        if not key:
            raise EndpointNotConfiguredError("GOOGLE_API_KEY is not set")
        from app.providers.gemini_provider import GeminiProvider

        return GeminiProvider(api_key=key, embed_model=model_id, embed_dimensions=dimensions)
    if p == "voyage":
        key = _secret(settings, "voyage_api_key", "VOYAGE_API_KEY")
        if not key:
            raise EndpointNotConfiguredError("VOYAGE_API_KEY is not set")
        from app.providers.voyage_provider import VoyageProvider

        return VoyageProvider(api_key=key, model=model_id)
    if p == "ollama":
        from app.core.config import get_provider_env

        base = get_provider_env("OLLAMA_BASE_URL") or _setting(settings, "ollama_base_url")
        if not base:
            raise EndpointNotConfiguredError("OLLAMA_BASE_URL is not set")
        from app.providers.ollama_provider import OllamaProvider

        return OllamaProvider(base_url=base, default_embed_model=model_id)
    raise EndpointNotConfiguredError(f"no embedding endpoint is known for provider {provider!r}")


def _openai_compatible(
    api_key: str,
    base_url: str | None,
    model_id: str,
    *,
    http_client: Any = None,
    dimensions: int | None = None,
) -> Any:
    from app.providers.openai_compatible import OpenAICompatibleProvider

    return OpenAICompatibleProvider(
        api_key=api_key,
        base_url=base_url,
        default_model=model_id,
        embed_model=model_id,
        http_client=http_client,
        embed_dimensions=dimensions,
    )


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def registry_output_dimensions(model: Any) -> int | None:
    """The output width the registry entry *model* asks for (``output_dimensions``)."""
    return _positive_int((getattr(model, "extra", None) or {}).get("output_dimensions"))


def _registry_base_url(model: Any) -> str:
    from app.ai_router.model_endpoints import normalize_base_url

    return normalize_base_url(str(getattr(model, "base_url", "") or ""))


def build_registry_model_embedder(
    model: Any, settings: Any, *, dimensions: int | None = None
) -> Any:
    """An embedder for ONE registry entry.

    The requested output width is *dimensions* when given (a same-model
    failover endpoint must produce the primary's width), else the entry's own
    ``output_dimensions``; it is sent as ``dimensions`` on ``/embeddings``.

    An entry with its own ``base_url`` is embedded at that OpenAI-compatible
    endpoint (``POST {base_url}/embeddings``): the URL is re-checked against the
    model-endpoint egress policy (raises
    :class:`app.ai_router.model_endpoints.ModelEndpointError` when refused), the
    connection is SSRF-pinned, and the credential is the entry's own
    (vault-decrypted) else the provider's env key. Any other entry is built on
    its provider's env endpoint (:func:`build_endpoint_embedder`).
    """
    base = _registry_base_url(model)
    provider = str(getattr(model, "provider", "") or "")
    model_id = str(getattr(model, "model_id", "") or "")
    out_dims = _positive_int(dimensions) or registry_output_dimensions(model)
    if not base:
        if out_dims:
            return build_endpoint_embedder(provider, model_id, settings, dimensions=out_dims)
        return build_endpoint_embedder(provider, model_id, settings)
    from app.ai_router.model_endpoints import (
        check_model_endpoint,
        endpoint_api_key,
        endpoint_http_client,
    )
    from app.providers.sdk_options import sdk_client_options

    checked = check_model_endpoint(base)
    api_key = endpoint_api_key(provider, model)
    return _openai_compatible(
        api_key,
        checked,
        model_id,
        http_client=endpoint_http_client(timeout=sdk_client_options()["timeout"]),
        dimensions=out_dims,
    )


# ── Dimensions ───────────────────────────────────────────────────────────────


def _registry_extra_int(provider: str, model_id: str, key: str) -> int | None:
    """A positive int field of the registry entry provider/model_id's ``extra``."""
    try:
        from app.ai_router.registry import model_registry

        entry = model_registry.get_configured(provider, model_id)
    except Exception:  # pragma: no cover - never block selection
        return None
    return _positive_int((getattr(entry, "extra", None) or {}).get(key)) if entry else None


def _recorded_dimension(provider: str, model_id: str) -> int | None:
    """The width a probe measured for the registry entry provider/model_id."""
    return _registry_extra_int(provider, model_id, "dimensions")


def embedding_model_dimension(provider: str, model_id: str, settings: Any = None) -> int | None:
    """Output width of *model_id*: the width a probe MEASURED for this registry
    entry ("Test connection" / first use), then the width the entry REQUESTS
    (``output_dimensions``, sent as ``dimensions``), then the catalog / known
    table, then the width the deployment declares for its own NVIDIA / on-prem
    model, else ``None``.

    A measurement wins over the request: an endpoint that ignores
    ``dimensions`` returns its native width, and that is what would be written.
    (Saving a new ``output_dimensions`` drops a measurement taken at another
    width, so a stale one never shadows the request.)"""
    from app.ai_router.model_catalog import catalog_embedding_dimension

    recorded = _recorded_dimension(provider, model_id)
    if recorded:
        return recorded
    requested = _registry_extra_int(provider, model_id, "output_dimensions")
    if requested:
        return requested
    known = catalog_embedding_dimension(model_id)
    if known:
        return known
    p = _norm(provider)
    for prov, model_attr, dim_attr in (
        ("nvidia", "nvidia_embed_model", "nvidia_embed_dim"),
        ("onprem", "onprem_embedding_model", "onprem_embedding_dim"),
    ):
        if p == prov and settings is not None and model_id == _setting(settings, model_attr):
            declared = getattr(settings, dim_attr, None)
            if isinstance(declared, int) and declared > 0:
                return declared
    return None


def embedding_dimension_status(
    provider: str, model_id: str, target_dim: int | None, settings: Any = None
) -> dict[str, Any]:
    """``{dimensions, dimension_mismatch, dimension_reason}`` for an API row."""
    dims = embedding_model_dimension(provider, model_id, settings)
    mismatch = bool(dims and target_dim and dims != target_dim)
    return {
        "dimensions": dims,
        "dimension_mismatch": mismatch,
        "dimension_reason": _mismatch_reason(f"{provider}/{model_id}", dims, target_dim)
        if mismatch
        else "",
    }


def _mismatch_reason(key: str, dims: int | None, target_dim: int | None) -> str:
    """Why *key* cannot be the DEFAULT embedder (it may still embed collections)."""
    from app.rag.store import SUPPORTED_EMBEDDING_DIMENSIONS

    base = (
        f"{key} produces {dims}-d vectors but the default embedding width is "
        f"{target_dim}-d (EMBEDDING_DIM), so it cannot be the default embedder "
        "(memory and default-bound collections use that width; making it the default "
        f"needs a re-index: EMBEDDING_DIM={dims} and re-embedding those collections)"
    )
    if dims in SUPPORTED_EMBEDDING_DIMENSIONS:
        return (
            f"{base}; a knowledge collection can still be bound to it "
            f"(knowledge_chunks_{dims}): create the collection with embedding_model={key}, "
            "or move one with POST /knowledge/collections/{id}/re-embed"
        )
    return (
        f"{base}, and there is no {dims}-d chunk table for collections (supported: "
        f"{', '.join(str(d) for d in SUPPORTED_EMBEDDING_DIMENSIONS)})"
    )


def record_embedding_dimension(model_id: str, base_url: str | None, dimensions: int) -> None:
    """Persist a measured width for *model_id* at *base_url* (best effort).

    Written to the shared registry store, so every replica / worker re-seeds
    with it; the in-process registry entry is updated at once.
    """
    try:
        from app.ai_router.registry import model_registry
        from app.ai_router.registry_store import get_model_registry_store

        wanted = (base_url or "").strip().rstrip("/")
        for m in model_registry.list_configured():
            if m.model_id == model_id and _registry_base_url(m) == wanted:
                m.extra = {**(m.extra or {}), "dimensions": dimensions}
        store = get_model_registry_store()
        if store is not None:
            store.record_dimension(model_id, base_url, dimensions)
    except Exception as exc:  # never fail an embed on bookkeeping
        logger.warning("embedding_dimension_record_failed", model=model_id, error=str(exc)[:200])


# ── First-use dimension check ────────────────────────────────────────────────


def _dimension_error(message: str) -> Exception:
    from app.rag.store import EmbeddingDimensionError

    return EmbeddingDimensionError(message)


class DimensionCheckedEmbedder:
    """Refuses vectors whose width differs from what the index expects.

    Wraps the registry-chosen embedder. *expected* is the vector index width
    (``EMBEDDING_DIM``), or ``None`` when there is none to hold it to. Every
    response is checked (one ``len`` per vector); the first one also records the
    model's measured width (:func:`record_embedding_dimension`) when it was not
    known. A mismatch raises :class:`app.rag.store.EmbeddingDimensionError`:
    the mismatched vectors are never returned, so they can never be written next
    to vectors of another width, and every later call is refused too.
    """

    def __init__(
        self,
        inner: Any,
        *,
        model_id: str,
        expected: int | None,
        base_url: str | None = None,
        known: int | None = None,
    ) -> None:
        self._inner = inner
        self.model_id = model_id
        # Read by embedder_model_name(): every chunk records this model (LAW-08).
        self._embed_model_name = model_id
        self._expected = expected
        self._probe_base_url = base_url
        self._measured: int | None = known
        self._recorded = known is not None
        self._refusal = ""

    @property
    def embedding_dim(self) -> int | None:
        """The measured width (``None`` until known); read by embedder_dimension()."""
        return self._measured

    @property
    def refusal(self) -> str:
        return self._refusal

    def _check(self, vectors: Sequence[Sequence[float]]) -> None:
        if self._refusal:
            raise _dimension_error(self._refusal)
        if not vectors:
            return
        widths = {len(v) for v in vectors}
        if len(widths) != 1:
            raise _dimension_error(
                f"embedding model {self.model_id} returned vectors of mixed widths "
                f"{sorted(widths)}"
            )
        width = widths.pop()
        if self._expected and width != self._expected:
            self._refusal = (
                f"embedding model {self.model_id} returned {width}-d vectors but the vector "
                f"index is {self._expected}-d (the default EMBEDDING_DIM, or the width of the "
                "collection bound to it); refusing to mix vector widths. Re-embed the "
                "collection (POST /knowledge/collections/{id}/re-embed) or bind a model of "
                "that width"
            )
            logger.error(
                "embedding_dimension_refused",
                model=self.model_id,
                dimension=width,
                expected=self._expected,
            )
            raise _dimension_error(self._refusal)
        if self._measured is None:
            self._measured = width
        if not self._recorded:
            self._recorded = True
            record_embedding_dimension(self.model_id, self._probe_base_url, width)

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        self._check([])
        response: EmbedResponse = await self._inner.embed(request)
        self._check(response.embeddings)
        return response

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self._check([])
        if not texts:
            return []
        batch = getattr(self._inner, "embed_batch", None)
        if callable(batch):
            vectors: list[list[float]] = await batch(texts)
        else:
            resp = await self._inner.embed(EmbedRequest(texts=texts, model=self.model_id))
            vectors = list(resp.embeddings)
        self._check(vectors)
        return vectors

    async def aclose(self) -> None:
        close = getattr(self._inner, "aclose", None)
        if callable(close):
            await close()

    def __getattr__(self, name: str) -> Any:
        # Everything else (complete, supports_*, endpoint_labels) is the wrapped one's.
        if name == "_inner":  # pragma: no cover - only while unpickling
            raise AttributeError(name)
        return getattr(self._inner, name)


# ── Same-model failover ──────────────────────────────────────────────────────


class SameModelFailoverEmbedder:
    """Embeds with ONE model id, failing over between endpoints that serve it.

    *endpoints* are ``(label, embedder)`` pairs, in registry order; every one of
    them must serve *model_id*. An endpoint that raises or times out is skipped
    for this call (logged as ``embedding_failover``) and tried last for
    ``cooldown_s``. The model id never changes: when every endpoint fails the
    last error is raised.
    """

    def __init__(
        self,
        model_id: str,
        endpoints: Sequence[tuple[str, Any]],
        *,
        timeout_s: float | None = _DEFAULT_FAILOVER_TIMEOUT_S,
        cooldown_s: float = _DEFAULT_COOLDOWN_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not model_id:
            raise ValueError("SameModelFailoverEmbedder needs a model id")
        if not endpoints:
            raise ValueError("SameModelFailoverEmbedder needs at least one endpoint")
        self.model_id = model_id
        # Read by embedder_model_name(): every chunk records this model (LAW-08).
        self._embed_model_name = model_id
        self._endpoints = list(endpoints)
        self._timeout_s = timeout_s if timeout_s and timeout_s > 0 else None
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._failed_at: dict[int, float] = {}

    @property
    def endpoint_labels(self) -> list[str]:
        return [label for label, _ in self._endpoints]

    def _order(self) -> list[int]:
        now = self._clock()
        healthy, cooling = [], []
        for idx in range(len(self._endpoints)):
            failed = self._failed_at.get(idx)
            if failed is not None and now - failed < self._cooldown_s:
                cooling.append(idx)
            else:
                healthy.append(idx)
        return healthy + cooling

    async def _call(self, operation: str, fn: Callable[[Any], Awaitable[_T]]) -> _T:
        order = self._order()
        last_exc: BaseException | None = None
        for pos, idx in enumerate(order):
            label, embedder = self._endpoints[idx]
            try:
                if self._timeout_s is None:
                    result = await fn(embedder)
                else:
                    result = await asyncio.wait_for(fn(embedder), self._timeout_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_exc = exc
                self._failed_at[idx] = self._clock()
                nxt = self._endpoints[order[pos + 1]][0] if pos + 1 < len(order) else None
                logger.warning(
                    "embedding_failover",
                    model=self.model_id,
                    operation=operation,
                    failed_endpoint=label,
                    next_endpoint=nxt,
                    error=f"{type(exc).__name__}: {str(exc)[:200]}",
                )
                continue
            self._failed_at.pop(idx, None)
            return result
        logger.error(
            "embedding_endpoints_exhausted",
            model=self.model_id,
            operation=operation,
            endpoints=self.endpoint_labels,
        )
        assert last_exc is not None
        raise last_exc

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        if request.model and request.model != self.model_id:
            # The caller asked for ANOTHER model explicitly: honour it on the
            # primary endpoint, but never fail it over (the other endpoints only
            # promise this wrapper's model).
            label, embedder = self._endpoints[self._order()[0]]
            logger.debug(
                "embedding_failover_skipped_other_model",
                model=self.model_id,
                requested=request.model,
                endpoint=label,
            )
            response: EmbedResponse = await embedder.embed(request)
            return response
        pinned = EmbedRequest(
            texts=request.texts, model=self.model_id, input_type=request.input_type
        )

        async def _embed(embedder: Any) -> EmbedResponse:
            response: EmbedResponse = await embedder.embed(pinned)
            return response

        return await self._call("embed", _embed)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        async def _batch(embedder: Any) -> list[list[float]]:
            batch = getattr(embedder, "embed_batch", None)
            if callable(batch):
                vectors: list[list[float]] = await batch(texts)
                return vectors
            resp = await embedder.embed(EmbedRequest(texts=texts, model=self.model_id))
            return list(resp.embeddings)

        return await self._call("embed_batch", _batch)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise NotImplementedError("SameModelFailoverEmbedder supports embeddings only")

    async def stream_tokens(
        self,
        request: CompletionRequest,
        on_token: Callable[[str], Awaitable[None]],
    ) -> CompletionResponse:
        raise NotImplementedError("SameModelFailoverEmbedder supports embeddings only")

    def supports_vision(self) -> bool:
        return False

    def supports_tool_use(self) -> bool:
        return False

    def supports_structured_output(self) -> bool:
        return False

    async def aclose(self) -> None:
        for _, embedder in self._endpoints:
            close = getattr(embedder, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception as exc:  # pragma: no cover - best effort
                    logger.warning("embedding_endpoint_close_failed", error=str(exc)[:200])


# ── Selection ────────────────────────────────────────────────────────────────


@dataclass
class RegistryEmbedderChoice:
    """Outcome of registry-driven selection.

    ``embedder`` is ``None`` when the preferred model was refused; ``refusal``
    says why, and the caller falls back to the env order.
    """

    embedder: Any = None
    model_id: str = ""
    provider: str = ""  # the primary endpoint's provider
    endpoints: list[str] = field(default_factory=list)
    dimension: int | None = None
    refusal: str = ""
    # (endpoint, reason) for every same-model endpoint that failed to build.
    errors: list[tuple[str, str]] = field(default_factory=list)


def _failover_timeout(settings: Any) -> float:
    raw = getattr(settings, "embedding_failover_timeout_s", None) or os.getenv(
        "EMBEDDING_FAILOVER_TIMEOUT_S", ""
    )
    try:
        return float(raw) if raw else _DEFAULT_FAILOVER_TIMEOUT_S
    except (TypeError, ValueError):
        return _DEFAULT_FAILOVER_TIMEOUT_S


def _refuse(choice: RegistryEmbedderChoice, reason: str) -> RegistryEmbedderChoice:
    choice.refusal = reason
    logger.warning(
        "embedding_registry_model_refused",
        model=choice.model_id or None,
        provider=choice.provider or None,
        reason=reason,
    )
    return choice


def select_registry_embedder(
    settings: Any, *, target_dim: int | None, registry: Any = None
) -> RegistryEmbedderChoice | None:
    """Build the embedder of the operator's preferred embedding model.

    Returns ``None`` when no embedding preference order is saved (the env order
    applies unchanged). Otherwise the first ELIGIBLE model of that order is used
    when servable here: it names its own endpoint (``base_url``) or its provider
    has credentials / an endpoint, and its width (measured, else known) equals
    *target_dim*. Every configured endpoint serving that SAME model id (registry
    order; plus the dedicated ``EMBEDDING_BASE_URL`` endpoint when it serves it)
    becomes a failover target. A refused model yields a choice with
    ``embedder=None`` and a ``refusal`` reason.
    """
    from app.ai_router.models import ModelCapability, TaskType
    from app.ai_router.registry import model_registry
    from app.ai_router.selection import model_key, ordered_configured_models

    reg = registry or model_registry
    # Seeds the process registry (env + saved overrides + preferences) first.
    models = ordered_configured_models(TaskType.EMBEDDING, registry=reg)
    preference = reg.preference_order(ModelCapability.EMBEDDING)
    if not preference:
        return None

    ranked = [m for m in models if model_key(m) in set(preference)]
    if not ranked:
        return _refuse(
            RegistryEmbedderChoice(),
            "no model of the saved embedding preference order is eligible here",
        )
    return _build_choice(ranked[0], models, settings, target_dim=target_dim)


def select_registry_endpoint_embedder(
    settings: Any, *, target_dim: int | None, registry: Any = None
) -> RegistryEmbedderChoice | None:
    """Precedence step 3: the deployment configures NO embedder in env, but the
    operator registered an embedding model with its own ``base_url``.

    The first such model in registry execution order (cheapest first, as for
    the reasoning roles' last resort) is used, with the same dimension safety
    as a preferred model. ``None`` when the registry has no such model.
    """
    from app.ai_router.models import TaskType
    from app.ai_router.registry import model_registry
    from app.ai_router.selection import ordered_configured_models

    reg = registry or model_registry
    models = ordered_configured_models(TaskType.EMBEDDING, registry=reg)
    with_endpoint = [
        m
        for m in models
        if _registry_base_url(m) and (m.extra or {}).get("source") == "override"
    ]
    if not with_endpoint:
        return None
    return _build_choice(with_endpoint[0], models, settings, target_dim=target_dim)


def _endpoint_label(provider: str, base_url: str) -> str:
    """``provider`` for an env endpoint, ``provider@host:port`` for a model's own."""
    if not base_url:
        return provider
    from urllib.parse import urlparse

    return f"{provider}@{urlparse(base_url).netloc or base_url}"


def _build_choice(
    primary: Any, models: list[Any], settings: Any, *, target_dim: int | None
) -> RegistryEmbedderChoice:
    """The embedder for registry model *primary* (and its same-model endpoints)."""
    from app.ai_router.model_endpoints import ModelEndpointError
    from app.ai_router.selection import model_key

    choice = RegistryEmbedderChoice()
    choice.model_id = str(primary.model_id)
    choice.provider = _norm(str(primary.provider))

    dims = embedding_model_dimension(str(primary.provider), choice.model_id, settings)
    if dims and target_dim and dims != target_dim:
        return _refuse(choice, _mismatch_reason(model_key(primary), dims, target_dim))

    endpoints: list[tuple[str, Any]] = []
    providers: list[str] = []
    seen: set[str] = set()  # endpoint identities: a base URL, else the provider
    providers_seen: set[str] = set()
    not_configured: list[str] = []
    same_model = [m for m in models if m.model_id == choice.model_id]
    # Every endpoint of the model must produce the same width: the primary's
    # requested width (if any) is requested from each of them.
    primary_out_dims = registry_output_dimensions(primary)
    # The chosen entry first, then the other endpoints of the same model in
    # registry order.
    for m in [primary, *[m for m in same_model if m is not primary]]:
        prov = _norm(str(m.provider))
        base = _registry_base_url(m)
        identity = base or f"provider:{prov}"
        if identity in seen:
            continue
        seen.add(identity)
        label = _endpoint_label(prov, base)
        try:
            embedder = build_registry_model_embedder(m, settings, dimensions=primary_out_dims)
        except EndpointNotConfiguredError as exc:
            not_configured.append(f"{prov}: {exc}")
            continue
        except ModelEndpointError as exc:  # refused by the egress policy / credential
            reason = str(exc)[:300]
            choice.errors.append((f"registry:{label}", reason))
            logger.error("embedder_provider_failed", provider=f"registry:{label}", reason=reason)
            continue
        except Exception as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:300]}"
            choice.errors.append((f"registry:{label}", reason))
            logger.error("embedder_provider_failed", provider=f"registry:{label}", reason=reason)
            continue
        # A provider endpoint that is the same server as a model's own URL
        # (e.g. ONPREM_EMBEDDING_BASE_URL) is one endpoint, not two.
        served_at = str(getattr(embedder, "_base_url", "") or "").strip().rstrip("/")
        if not base and served_at:
            if served_at in seen:
                continue
            seen.add(served_at)
        if not base:
            providers_seen.add(prov)
        endpoints.append((label, embedder))
        providers.append(prov)

    dedicated = _dedicated_endpoint(
        settings, choice.model_id, providers_seen, seen, dimensions=primary_out_dims
    )
    if dedicated is not None:
        endpoints.append(dedicated)
        providers.append("dedicated")

    if not endpoints:
        detail = "; ".join(not_configured + [f"{p}: {r}" for p, r in choice.errors])
        return _refuse(
            choice,
            f"no endpoint serving embedding model {choice.model_id} is servable here"
            + (f" ({detail})" if detail else ""),
        )

    # The provider NAME only (never a URL): it is shown on the public /health.
    choice.provider = providers[0]
    choice.endpoints = [label for label, _ in endpoints]
    choice.dimension = dims
    inner: Any
    if len(endpoints) == 1:
        inner = endpoints[0][1]
    else:
        inner = SameModelFailoverEmbedder(
            choice.model_id, endpoints, timeout_s=_failover_timeout(settings)
        )
    choice.embedder = DimensionCheckedEmbedder(
        inner,
        model_id=choice.model_id,
        expected=target_dim or dims,
        base_url=_registry_base_url(primary) or None,
        known=dims,
    )
    logger.info(
        "embedder_registry_selected",
        model=choice.model_id,
        endpoints=choice.endpoints,
        dimension=dims,
        target_dim=target_dim,
    )
    return choice


def _dedicated_endpoint(
    settings: Any,
    model_id: str,
    providers_seen: set[str],
    urls_seen: set[str],
    *,
    dimensions: int | None = None,
) -> tuple[str, Any] | None:
    """The dedicated ``EMBEDDING_BASE_URL`` endpoint when it serves *model_id*
    and is not already one of the registry endpoints."""
    from app.ai_router.model_catalog import provider_for_endpoint_url

    base = _setting(settings, "embedding_base_url") or os.getenv("EMBEDDING_BASE_URL", "")
    model = _setting(settings, "embedding_model") or os.getenv("EMBEDDING_MODEL", "")
    if not base or model != model_id:
        return None
    if base.strip().rstrip("/") in urls_seen:
        return None
    if (provider_for_endpoint_url(base) or "") in providers_seen:
        return None
    key = (
        _setting(settings, "embedding_api_key")
        or os.getenv("EMBEDDING_API_KEY", "")
        or "sk-noauth"
    )
    try:
        return ("dedicated", _openai_compatible(key, base, model_id, dimensions=dimensions))
    except Exception as exc:
        logger.error(
            "embedder_provider_failed",
            provider="registry:dedicated",
            reason=f"{type(exc).__name__}: {str(exc)[:300]}",
        )
        return None
