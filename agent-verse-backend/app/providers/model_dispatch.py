"""Send each request to the provider that serves its model.

The platform resolves ONE LLM provider (the on-prem/NVIDIA cluster or the first
configured key), and every call passes the selected ``request.model`` to it. A
model registered in the Model Registry for another provider — a Groq, Claude,
OpenAI, Gemini, xAI or Ollama model picked by the preference order or reached by
failover — was therefore sent to the wrong API and failed.

:class:`ModelDispatchProvider` wraps the platform provider. For a model that a
registry override (UI / catalog import) assigns to a *different*, credentialed
provider, it calls that provider's adapter (built once, from the deployment's
env keys); everything else goes to the wrapped provider exactly as before —
env-seeded models, models the wrapped cluster serves, unknown models. A
model registered with its own ``base_url`` (e.g. a vLLM server on the LAN) is
called at that URL, after the model-endpoint egress check
(``app.ai_router.model_endpoints``). It is
transparent otherwise (``__getattr__`` delegation, like ``TracedProvider``).
Embeddings always use the wrapped provider (an index's vectors must come from
one model).
"""

from __future__ import annotations

import threading
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

_ALIASES = {"google": "gemini", "openai_compatible": "openai"}


def _norm(provider: str) -> str:
    p = (provider or "").strip().lower()
    return _ALIASES.get(p, p)


def _env(name: str) -> str:
    from app.core.config import get_provider_env

    return get_provider_env(name)


def _provider_config(provider: str, model: str) -> Any | None:
    """A ProviderConfig for *provider* from the deployment's credentials, or None."""
    import os

    from app.providers.registry import ProviderConfig

    p = _norm(provider)
    if p == "anthropic":
        key = _env("ANTHROPIC_API_KEY")
        return ProviderConfig("anthropic", api_key=key, models=[model]) if key else None
    if p == "openai":
        key = _env("OPENAI_API_KEY")
        base = os.getenv("OPENAI_BASE_URL", "") or "https://api.openai.com/v1"
        return ProviderConfig("openai", base_url=base, api_key=key, models=[model]) if key else None
    if p == "nvidia":
        key = _env("NVIDIA_API_KEY")
        base = _env("NVIDIA_BASE_URL") or "https://integrate.api.nvidia.com/v1"
        return ProviderConfig("nvidia", base_url=base, api_key=key, models=[model]) if key else None
    if p == "groq":
        key = _env("GROQ_API_KEY")
        return (
            ProviderConfig(
                "groq", base_url="https://api.groq.com/openai/v1", api_key=key, models=[model]
            )
            if key
            else None
        )
    if p == "gemini":
        key = _env("GOOGLE_API_KEY")
        return ProviderConfig("gemini", api_key=key, models=[model]) if key else None
    if p == "xai":
        key = _env("XAI_API_KEY")
        return ProviderConfig("xai", api_key=key, models=[model]) if key else None
    if p == "openrouter":
        key = _env("OPENROUTER_API_KEY")
        return ProviderConfig("openrouter", api_key=key, models=[model]) if key else None
    if p == "ollama":
        base = _env("OLLAMA_BASE_URL")
        return ProviderConfig("ollama", base_url=base, models=[model]) if base else None
    return None


def _overrides(model: str) -> list[Any]:
    """Registry OVERRIDES (UI / catalog) for *model* that may serve now."""
    try:
        from app.ai_router.registry import model_registry
        from app.ai_router.selection import is_eligible

        return [
            m
            for m in model_registry.list_configured()
            if m.model_id == model
            and (m.extra or {}).get("source") == "override"
            and is_eligible(m)
        ]
    except Exception:  # pragma: no cover - never block a call
        return []


def _override_providers(model: str) -> list[str]:
    """Providers that registry OVERRIDES assign *model* to, in execution order."""
    return [m.provider for m in _overrides(model)]


def _endpoint_overrides(model: str) -> list[Any]:
    """Overrides of *model* that name their own endpoint (``base_url``)."""
    try:
        from app.ai_router.registry import model_registry

        return [
            m
            for m in model_registry.list_configured()
            if m.model_id == model
            and getattr(m, "base_url", None)
            and (m.extra or {}).get("source") == "override"
        ]
    except Exception:  # pragma: no cover - never block a call
        return []


class ModelDispatchProvider:
    """The platform provider, plus per-model dispatch to other providers."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._adapters: dict[str, Any] = {}
        self._lock = threading.Lock()

    @property
    def inner(self) -> Any:
        return self._inner

    def _adapter(self, provider: str, model: str) -> Any | None:
        key = _norm(provider)
        with self._lock:
            if key in self._adapters:
                return self._adapters[key]
        adapter: Any = None
        try:
            from app.providers.registry import _instantiate_provider

            cfg = _provider_config(key, model)
            adapter = _instantiate_provider(cfg) if cfg is not None else None
            if adapter is not None and not getattr(adapter, "_agentverse_provider_type", None):
                adapter._agentverse_provider_type = key
        except Exception as exc:
            logger.warning(
                "model_dispatch_adapter_failed provider=%s error=%s", key, str(exc)[:200]
            )
            adapter = None
        with self._lock:
            self._adapters.setdefault(key, adapter)
            return self._adapters[key]

    def _endpoint_adapter(self, endpoint: Any) -> Any | None:
        """An OpenAI-compatible client for a model's own ``base_url`` (cached).

        The URL is re-checked against the model-endpoint egress policy here, so
        an allowlist change takes effect on the next adapter build.
        """
        from app.ai_router.model_endpoints import (
            check_model_endpoint,
            endpoint_api_key,
            onprem_extra_body,
        )

        # The saved credential (ciphertext) is part of the key: re-entering it
        # in the registry builds a new client instead of reusing the old one.
        secret = str((getattr(endpoint, "extra", None) or {}).get("api_key_encrypted") or "")
        key = f"url:{endpoint.provider}|{endpoint.base_url}|{hash(secret) if secret else ''}"
        with self._lock:
            if key in self._adapters:
                return self._adapters[key]
        adapter: Any = None
        try:
            from app.providers.openai_compatible import OpenAICompatibleProvider

            base = check_model_endpoint(str(endpoint.base_url))
            adapter = OpenAICompatibleProvider(
                api_key=endpoint_api_key(endpoint.provider, endpoint),
                base_url=base,
                default_model=endpoint.model_id,
                embed_model=endpoint.model_id,
                extra_body=onprem_extra_body(endpoint.provider),
            )
            adapter._agentverse_provider_type = _norm(endpoint.provider) or "openai_compatible"
        except Exception as exc:
            logger.warning(
                "model_dispatch_endpoint_refused model=%s error=%s",
                endpoint.model_id, str(exc)[:200],
            )
            adapter = None
        with self._lock:
            self._adapters.setdefault(key, adapter)
            return self._adapters[key]

    def target_for(self, model: str | None) -> Any:
        """The provider object that should serve *model*."""
        if not model:
            return self._inner
        # A model registered with its own endpoint (vLLM / Ollama / on-prem URL)
        # is served there, whatever the platform provider is.
        for endpoint in _endpoint_overrides(model):
            adapter = self._endpoint_adapter(endpoint)
            if adapter is not None:
                return adapter
        endpoints = getattr(self._inner, "_endpoints", None)
        if isinstance(endpoints, dict) and model in endpoints:
            return self._inner  # the wrapped cluster serves it itself
        providers = [_norm(p) for p in _override_providers(model)]
        if not providers:
            return self._inner  # env-seeded / unknown: today's behaviour
        base_type = _norm(str(getattr(self._inner, "_agentverse_provider_type", "") or ""))
        if "onprem" in providers or (base_type in providers and not isinstance(endpoints, dict)):
            return self._inner
        for provider in providers:
            adapter = self._adapter(provider, model)
            if adapter is not None:
                return adapter
        return self._inner

    # -- dispatched paths --------------------------------------------------------

    async def complete(self, request: Any) -> Any:
        return await self.target_for(getattr(request, "model", None)).complete(request)

    async def stream_complete(self, request: Any) -> AsyncGenerator[str, None]:
        async for chunk in self.target_for(getattr(request, "model", None)).stream_complete(
            request
        ):
            yield chunk

    async def stream_tokens(
        self, request: Any, on_token: Callable[[str], Awaitable[None]]
    ) -> Any:
        return await self.target_for(getattr(request, "model", None)).stream_tokens(
            request, on_token
        )

    # -- transparent delegation for everything else (embeddings included) -------

    _OWN_ATTRS = frozenset({"_inner", "_adapters", "_lock"})

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        # Attributes set on the platform provider after it is resolved (labels,
        # circuit scope, test doubles) belong to the wrapped provider.
        if name in self._OWN_ATTRS:
            object.__setattr__(self, name, value)
        else:
            setattr(self._inner, name, value)


def can_serve_model(provider: Any, model: str) -> bool:
    """True when *provider* (or a provider it wraps) routes *model* to a server.

    A :class:`ModelDispatchProvider` routes every registry model (own
    ``base_url``, override provider, or its cluster); a multi-endpoint cluster
    serves the models in its ``_endpoints``. Any other provider is assumed to
    serve only its own default model — a role's model is never sent to an API
    that cannot serve it.
    """
    if not model:
        return False
    current = provider
    for _ in range(8):  # wrapper chains are short; bound the walk
        if current is None:
            return False
        if isinstance(current, ModelDispatchProvider):
            return True
        own = getattr(current, "__dict__", {})
        endpoints = own.get("_endpoints")
        if isinstance(endpoints, dict) and model in endpoints:
            return True
        if own.get("_default_model") == model:
            return True
        current = own.get("_inner") or own.get("_provider")
    return False


def with_model_dispatch(provider: Any) -> Any:
    """Wrap a real platform provider; placeholders and ``None`` pass through."""
    if provider is None or isinstance(provider, ModelDispatchProvider):
        return provider
    from app.providers.fake import FakeProvider

    if isinstance(provider, FakeProvider):
        return provider
    return ModelDispatchProvider(provider)
