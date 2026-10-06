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
    "EndpointNotConfiguredError",
    "RegistryEmbedderChoice",
    "SameModelFailoverEmbedder",
    "build_endpoint_embedder",
    "embedding_dimension_status",
    "embedding_model_dimension",
    "select_registry_embedder",
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


def build_endpoint_embedder(provider: str, model_id: str, settings: Any) -> Any:
    """An embedder producing *model_id* vectors on *provider*'s endpoint.

    Raises :class:`EndpointNotConfiguredError` when this deployment has no
    credentials / endpoint for *provider* (not servable here).
    """
    p = _norm(provider)
    if p == "nvidia":
        key = _secret(settings, "nvidia_api_key", "NVIDIA_API_KEY")
        if not key:
            raise EndpointNotConfiguredError("NVIDIA_API_KEY is not set")
        base = _setting(settings, "nvidia_base_url") or "https://integrate.api.nvidia.com/v1"
        return _openai_compatible(key, base, model_id)
    if p == "onprem":
        base = _setting(settings, "onprem_embedding_base_url")
        if not (getattr(settings, "onprem_enabled", False) and base):
            raise EndpointNotConfiguredError(
                "no on-prem embedding endpoint (ONPREM_ENABLED + ONPREM_EMBEDDING_BASE_URL)"
            )
        return _openai_compatible(_setting(settings, "onprem_api_key") or "EMPTY", base, model_id)
    if p == "openai":
        key = _secret(settings, "openai_api_key", "OPENAI_API_KEY")
        if not key:
            raise EndpointNotConfiguredError("OPENAI_API_KEY is not set")
        return _openai_compatible(key, os.getenv("OPENAI_BASE_URL") or None, model_id)
    if p == "gemini":
        key = _secret(settings, "google_api_key", "GOOGLE_API_KEY")
        if not key:
            raise EndpointNotConfiguredError("GOOGLE_API_KEY is not set")
        from app.providers.gemini_provider import GeminiProvider

        return GeminiProvider(api_key=key, embed_model=model_id)
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


def _openai_compatible(api_key: str, base_url: str | None, model_id: str) -> Any:
    from app.providers.openai_compatible import OpenAICompatibleProvider

    return OpenAICompatibleProvider(
        api_key=api_key, base_url=base_url, default_model=model_id, embed_model=model_id
    )


# ── Dimensions ───────────────────────────────────────────────────────────────


def embedding_model_dimension(provider: str, model_id: str, settings: Any = None) -> int | None:
    """Output width of *model_id* (catalog / known table first, then the width
    the deployment declares for its own NVIDIA / on-prem model), else ``None``."""
    from app.ai_router.model_catalog import catalog_embedding_dimension

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
    return (
        f"{key} produces {dims}-d vectors but the vector index is {target_dim}-d "
        "(EMBEDDING_DIM); switching to it needs a re-index"
    )


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
    when servable here: its provider has credentials / an endpoint and its known
    width equals *target_dim*. Every configured endpoint serving that SAME model
    id (registry order; plus the dedicated ``EMBEDDING_BASE_URL`` endpoint when
    it serves it) becomes a failover target. A refused model yields a choice with
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

    choice = RegistryEmbedderChoice()
    ranked = [m for m in models if model_key(m) in set(preference)]
    if not ranked:
        return _refuse(
            choice, "no model of the saved embedding preference order is eligible here"
        )
    primary = ranked[0]
    choice.model_id = str(primary.model_id)
    choice.provider = _norm(str(primary.provider))

    dims = embedding_model_dimension(choice.provider, choice.model_id, settings)
    if dims and target_dim and dims != target_dim:
        return _refuse(choice, _mismatch_reason(model_key(primary), dims, target_dim))

    endpoints: list[tuple[str, Any]] = []
    providers_seen: set[str] = set()
    not_configured: list[str] = []
    same_model = [m for m in models if m.model_id == choice.model_id]
    # The preferred entry first, then the other endpoints of the same model in
    # registry order.
    for m in [primary, *[m for m in same_model if m is not primary]]:
        prov = _norm(str(m.provider))
        if prov in providers_seen:
            continue
        providers_seen.add(prov)
        try:
            endpoints.append((prov, build_endpoint_embedder(prov, choice.model_id, settings)))
        except EndpointNotConfiguredError as exc:
            not_configured.append(f"{prov}: {exc}")
        except Exception as exc:
            reason = f"{type(exc).__name__}: {str(exc)[:300]}"
            choice.errors.append((f"registry:{prov}", reason))
            logger.error("embedder_provider_failed", provider=f"registry:{prov}", reason=reason)

    dedicated = _dedicated_endpoint(settings, choice.model_id, providers_seen)
    if dedicated is not None:
        endpoints.append(dedicated)

    if not endpoints:
        detail = "; ".join(not_configured + [f"{p}: {r}" for p, r in choice.errors])
        return _refuse(
            choice,
            f"no endpoint serving embedding model {choice.model_id} is servable here"
            + (f" ({detail})" if detail else ""),
        )

    choice.provider = endpoints[0][0]
    choice.endpoints = [label for label, _ in endpoints]
    choice.dimension = dims
    if len(endpoints) == 1:
        choice.embedder = endpoints[0][1]
    else:
        choice.embedder = SameModelFailoverEmbedder(
            choice.model_id, endpoints, timeout_s=_failover_timeout(settings)
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
    settings: Any, model_id: str, providers_seen: set[str]
) -> tuple[str, Any] | None:
    """The dedicated ``EMBEDDING_BASE_URL`` endpoint when it serves *model_id*
    and is not already one of the registry endpoints."""
    from app.ai_router.model_catalog import provider_for_endpoint_url

    base = _setting(settings, "embedding_base_url") or os.getenv("EMBEDDING_BASE_URL", "")
    model = _setting(settings, "embedding_model") or os.getenv("EMBEDDING_MODEL", "")
    if not base or model != model_id:
        return None
    if (provider_for_endpoint_url(base) or "") in providers_seen:
        return None
    key = (
        _setting(settings, "embedding_api_key")
        or os.getenv("EMBEDDING_API_KEY", "")
        or "sk-noauth"
    )
    try:
        return ("dedicated", _openai_compatible(key, base, model_id))
    except Exception as exc:
        logger.error(
            "embedder_provider_failed",
            provider="registry:dedicated",
            reason=f"{type(exc).__name__}: {str(exc)[:300]}",
        )
        return None
