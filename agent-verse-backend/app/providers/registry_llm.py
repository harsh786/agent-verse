"""The platform LLM when no env provider is configured: the Model Registry's models.

A deployment may configure its LLMs ONLY in the Model Registry: operator-added
models with their own ``base_url`` and a vault-encrypted per-model API key, and
no ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY`` / ``NVIDIA_API_KEY`` in the server
env. The platform resolved its provider from env keys only, so it fell back to
the canned ``FakeProvider`` (or, outside development, the failing stand-in), and
:class:`~app.providers.model_dispatch.ModelDispatchProvider` was never built on
top of it: goals planned "Complete the requested task" and answered "Task
executed successfully" while the registry's real models sat unused.

:class:`RegistryLLMProvider` is that platform provider. It resolves the serving
model PER CALL from the shared registry, so a model added after startup is used
on the next call (the registry re-seeds when the shared store's version
changes) — no restart:

* a request for a registry model goes to that model (its own endpoint, or its
  provider's API with its own key);
* a request for no model, or for a model nothing here can serve (e.g. an
  env-seeded default with no key), goes to the first usable text-generation
  model in the registry's execution order (the operator's preference order,
  then cheapest);
* with no usable registry model it falls through to the placeholder it wraps:
  the canned ``FakeProvider`` in development / test (logged loudly), the
  failing :class:`~app.providers.llm_resolution.UnconfiguredLLMProvider`
  everywhere else ("no LLM configured: add a model in Model Registry or set a
  provider key").
"""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from app.observability.logging import get_logger
from app.providers.model_dispatch import (
    ModelDispatchProvider,
    _norm,
    _provider_config,
    keyed_endpoint_usable,
)

logger = get_logger(__name__)

__all__ = [
    "REGISTRY_PROVIDER_TYPE",
    "RegistryLLMProvider",
    "configured_text_model",
    "is_registry_llm",
    "live_platform_provider",
    "registry_backed_provider",
    "registry_has_usable_llm",
    "usable_registry_text_models",
]

REGISTRY_PROVIDER_TYPE = "registry"


def _servable_without_platform(m: Any) -> bool:
    """Whether *m* can be called with no env platform provider behind it."""
    if getattr(m, "base_url", None):
        return True  # its own OpenAI-compatible endpoint
    if keyed_endpoint_usable(m):
        return True  # its own saved key on its provider's API
    try:
        return _provider_config(str(getattr(m, "provider", "")), str(m.model_id)) is not None
    except Exception:  # pragma: no cover - never block resolution
        return False


def usable_registry_text_models() -> list[Any]:
    """Registry text-generation models callable without an env platform provider.

    In execution order (preference order, then cheapest). Empty when the
    registry has none — or cannot be read.
    """
    try:
        from app.ai_router.models import TaskType
        from app.ai_router.selection import ordered_configured_models

        ordered = ordered_configured_models(TaskType.TEXT_GENERATION)
    except Exception as exc:  # never block a call on the registry
        logger.warning("registry_llm_lookup_failed", error=str(exc)[:200])
        return []
    return [m for m in ordered if _servable_without_platform(m)]


def registry_has_usable_llm() -> bool:
    return bool(usable_registry_text_models())


def configured_text_model(model: str) -> Any | None:
    """The registry's eligible text-generation entry for *model*, or None."""
    if not model:
        return None
    try:
        from app.ai_router.models import TaskType
        from app.ai_router.selection import ordered_configured_models

        for m in ordered_configured_models(TaskType.TEXT_GENERATION):
            if m.model_id == model:
                return m
    except Exception as exc:  # pragma: no cover - never block a call
        logger.warning("registry_llm_lookup_failed", error=str(exc)[:200])
    return None


class RegistryLLMProvider(ModelDispatchProvider):
    """The platform provider of a registry-only deployment (see module doc)."""

    _OWN_ATTRS = ModelDispatchProvider._OWN_ATTRS | frozenset({"_fake_warned"})

    def __init__(self, placeholder: Any) -> None:
        super().__init__(placeholder)
        self._fake_warned = False

    # -- state ---------------------------------------------------------------

    def has_models(self) -> bool:
        """Whether a registry model can serve a call right now."""
        return registry_has_usable_llm()

    def serves_fake(self) -> bool:
        """True when a call now would be answered by the canned FakeProvider."""
        from app.providers.fake import FakeProvider
        from app.providers.llm_resolution import UnconfiguredLLMProvider

        inner = self._inner
        return (
            isinstance(inner, FakeProvider)
            and not isinstance(inner, UnconfiguredLLMProvider)
            and not self.has_models()
        )

    @property
    def _default_model(self) -> str:
        usable = usable_registry_text_models()
        if usable:
            return str(usable[0].model_id)
        return str(getattr(self._inner, "_default_model", "") or "")

    @property
    def _agentverse_provider_type(self) -> str:
        usable = usable_registry_text_models()
        if usable:
            return _norm(str(getattr(usable[0], "provider", "") or "")) or REGISTRY_PROVIDER_TYPE
        return REGISTRY_PROVIDER_TYPE

    # -- routing ---------------------------------------------------------------

    def route(self, request: Any) -> tuple[Any, Any]:
        """``(provider, request)`` for *request*; the request's model may be rewritten."""
        requested = str(getattr(request, "model", "") or "")
        usable = usable_registry_text_models()
        if requested and any(m.model_id == requested for m in usable):
            target = self.target_for(requested)
            if target is not self._inner:
                return target, request
        for m in usable:
            if m.model_id == requested:
                continue
            target = self.target_for(str(m.model_id))
            if target is not self._inner:
                logger.debug(
                    "registry_llm_default_model", requested=requested, model=m.model_id
                )
                return target, _with_model(request, str(m.model_id))
        self._note_placeholder()
        return self._inner, request

    def _note_placeholder(self) -> None:
        from app.providers.fake import FakeProvider
        from app.providers.llm_resolution import UnconfiguredLLMProvider

        inner = self._inner
        is_fake = isinstance(inner, FakeProvider) and not isinstance(
            inner, UnconfiguredLLMProvider
        )
        if is_fake and not self._fake_warned:
            self._fake_warned = True
            logger.warning(
                "llm_fake_provider_serving",
                message=(
                    "No LLM is configured: the canned FakeProvider answers (development "
                    "only). Add a model in the Model Registry or set a provider key."
                ),
            )

    async def complete(self, request: Any) -> Any:
        target, req = self.route(request)
        return await target.complete(req)

    async def stream_complete(self, request: Any) -> AsyncGenerator[str, None]:
        target, req = self.route(request)
        async for chunk in target.stream_complete(req):
            yield chunk

    async def stream_tokens(
        self, request: Any, on_token: Callable[[str], Awaitable[None]]
    ) -> Any:
        target, req = self.route(request)
        return await target.stream_tokens(req, on_token)


def _with_model(request: Any, model: str) -> Any:
    if dataclasses.is_dataclass(request) and not isinstance(request, type):
        return dataclasses.replace(request, model=model)
    copy = getattr(request, "model_copy", None)
    if callable(copy):
        return copy(update={"model": model})
    request.model = model
    return request


_SHARED: dict[bool, RegistryLLMProvider] = {}
_SHARED_LOCK = threading.Lock()


def registry_backed_provider(placeholder: Any = None) -> RegistryLLMProvider:
    """A :class:`RegistryLLMProvider` over *placeholder* (default: the env's one).

    Without an explicit placeholder one shared instance per process (and
    fake-allowed mode) is returned, so its endpoint clients are reused across
    goals.
    """
    if placeholder is not None:
        return RegistryLLMProvider(placeholder)
    from app.providers.llm_resolution import fake_llm_allowed

    fake = fake_llm_allowed()
    with _SHARED_LOCK:
        shared = _SHARED.get(fake)
        if shared is None:
            shared = RegistryLLMProvider(_placeholder(fake))
            _SHARED[fake] = shared
        return shared


def _placeholder(fake_allowed: bool) -> Any:
    from app.providers.llm_resolution import UnconfiguredLLMProvider

    if not fake_allowed:
        return UnconfiguredLLMProvider()
    from app.providers.registry import fake_dev_provider

    return fake_dev_provider()


def reset_shared_registry_provider() -> None:
    """Drop the shared instances (tests)."""
    with _SHARED_LOCK:
        _SHARED.clear()


def is_registry_llm(provider: Any) -> bool:
    return isinstance(provider, RegistryLLMProvider)


def live_platform_provider(provider: Any) -> Any | None:
    """*provider* for wiring a long-lived service, or None when it is no real LLM.

    A :class:`RegistryLLMProvider` is kept even when the registry is empty right
    now: it resolves per call, so a model added later is used without a
    restart (callers re-check :func:`~app.providers.llm_resolution.is_placeholder_provider`
    per call). Plain placeholders (FakeProvider / the unconfigured stand-in) → None.
    """
    from app.providers.fake import FakeProvider

    if provider is None:
        return None
    if is_registry_llm(provider):
        return provider
    return None if isinstance(provider, FakeProvider) else provider


# ── startup / late binding ───────────────────────────────────────────────────


def _entry_usable(e: dict[str, Any]) -> bool:
    """A persisted registry entry (store dict) that can serve text generation."""
    caps = {str(c) for c in (e.get("capabilities") or [])}
    if "text_generation" not in caps or not e.get("model_id"):
        return False
    if e.get("is_available") is False:
        return False
    if str(e.get("base_url") or "").strip():
        return True
    provider = _norm(str(e.get("provider") or ""))
    from app.providers.model_dispatch import KEYED_ENDPOINT_PROVIDERS

    if str(e.get("api_key_encrypted") or "").strip() and provider in KEYED_ENDPOINT_PROVIDERS:
        return True
    try:
        return _provider_config(provider, str(e["model_id"])) is not None
    except Exception:  # pragma: no cover - defensive
        return False


def registry_llm_configured_at_startup(redis_url: str | None) -> bool:
    """Whether the shared Model Registry already holds a usable text model.

    Used by the production start-up guard, which runs before the lifespan wires
    the registry store: reads the store directly (short timeouts; any failure
    means "unknown" → False, and the guard keeps refusing to start).
    """
    from app.ai_router.registry_store import ModelRegistryStore, get_model_registry_store

    store = get_model_registry_store()
    if store is None:
        if not redis_url:
            return False
        try:
            import redis as _sync_redis

            client = _sync_redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=1.5,
            )
            store = ModelRegistryStore(client)
        except Exception as exc:
            logger.warning("registry_llm_startup_probe_failed", error=str(exc)[:200])
            return False
    try:
        return any(_entry_usable(e) for e in store.list())
    except Exception as exc:
        logger.warning("registry_llm_startup_probe_failed", error=str(exc)[:200])
        return False


_LATE_BIND_INTERVAL_S = 15.0


def _registry_version() -> int | None:
    from app.ai_router.registry_store import get_model_registry_store

    store = get_model_registry_store()
    return store.version() if store is not None else None


async def watch_for_late_registry_llm(
    bind: Callable[[], object],
    *,
    is_bound: Callable[[], bool],
    version: Callable[[], int | None] = _registry_version,
    interval_s: float = _LATE_BIND_INTERVAL_S,
) -> None:
    """Bind a text model registered in the Model Registry AFTER the API started.

    The API process resolves its platform provider once; with nothing in env
    and nothing in the registry yet it holds the placeholder. Goals already
    resolve the registry per goal, but services reading ``app.state`` (and its
    ``llm_provider``) would keep the placeholder until a restart. This polls the
    shared registry's version every ``interval_s`` and calls ``bind`` when it
    changed, until ``is_bound()``. Never raises.
    """
    import asyncio

    def _version() -> int | None:
        try:
            return version()
        except Exception as exc:  # never die on a registry read
            logger.warning("registry_llm_late_bind_version_unreadable", error=str(exc)[:200])
            return None

    last = _version()
    while not is_bound():
        await asyncio.sleep(interval_s)
        current = _version()
        if current is None or current == last:
            continue
        last = current
        try:
            await asyncio.to_thread(bind)
        except Exception as exc:
            logger.warning("registry_llm_late_bind_failed", error=str(exc)[:200])
        if is_bound():
            logger.info("llm_late_bound_from_registry")
