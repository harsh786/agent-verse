"""Which LLM provider serves a tenant: tenant BYOK, then the platform, else an error.

One resolver for goals and workflows (BYOK-3). Workflows used to get ONE
process-wide provider at worker start (``resolve_provider()``): a tenant's own
key saved under Settings → LLM Providers was never used, and with no platform
key the step got the canned FakeProvider ("Task executed successfully") as if a
model had answered.

Order, identical to the goal worker (``app.scaling.tasks.run_goal``):

1. the tenant's BYOK config (durable store, strict read; a configured but
   unusable key raises :class:`TenantProviderError` — never a silent platform
   fallback);
2. the platform provider (on-prem deployment cluster, then the env registry);
3. otherwise :class:`NoLLMProviderConfiguredError`.

FakeProvider is never part of it. Outside development/test
(:func:`fake_llm_allowed`) ``resolve_provider()`` returns
:class:`UnconfiguredLLMProvider`, which fails every call with that error.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from app.providers.base import CompletionRequest, CompletionResponse, EmbedRequest, EmbedResponse
from app.providers.fake import FakeProvider
from app.providers.tenant_provider import TenantProviderError

# Same set as the vault's dev-key environments (SECRET-05): the published dev
# key and the canned FakeProvider are both development/test conveniences only.
FAKE_LLM_ENVIRONMENTS = frozenset({"development", "dev", "local", "test", "testing"})


def fake_llm_allowed() -> bool:
    """True only in an explicit development / test environment."""
    return os.getenv("ENVIRONMENT", "development").strip().lower() in FAKE_LLM_ENVIRONMENTS


def platform_key_required() -> bool:
    """Whether production refuses to start without a platform LLM key.

    ``LLM_REQUIRE_PLATFORM_KEY`` (default true). Set it to false for a BYOK-only
    deployment (owner decision 2026-10-05): the platform then runs with the
    failing :class:`UnconfiguredLLMProvider`, tenants with their own key run
    normally and tenants without one get "no LLM provider configured".
    """
    raw = os.getenv("LLM_REQUIRE_PLATFORM_KEY", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


class NoLLMProviderConfiguredError(TenantProviderError):
    """Neither the tenant nor the platform has an LLM provider configured."""


def no_provider_message(tenant_id: str | None = None) -> str:
    who = f"tenant {tenant_id!r}" if tenant_id else "this request"
    return (
        f"no LLM provider configured for {who}: add a model in the Model Registry, save an "
        "API key under Settings → LLM Providers (PUT /tenants/me/llm) or set a platform "
        "provider key"
    )


class UnconfiguredLLMProvider(FakeProvider):
    """Stand-in for "no platform LLM" outside development: every call FAILS.

    It subclasses :class:`FakeProvider` only so the many existing
    ``isinstance(p, FakeProvider)`` checks ("no real LLM configured → treat as
    absent / degrade") keep treating it as absent. Unlike FakeProvider it never
    returns canned output.
    """

    def __init__(self) -> None:
        super().__init__(responses=[""])

    def _fail(self) -> NoLLMProviderConfiguredError:
        return NoLLMProviderConfiguredError(no_provider_message())

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise self._fail()

    async def stream_complete(self, request: CompletionRequest) -> AsyncGenerator[str, None]:
        for _ in ():  # an async generator, like FakeProvider's: fails on first pull
            yield ""
        raise self._fail()

    async def stream_tokens(
        self,
        request: CompletionRequest,
        on_token: Callable[[str], Awaitable[None]],
    ) -> CompletionResponse:
        raise self._fail()

    async def embed(self, request: EmbedRequest) -> EmbedResponse:
        raise self._fail()

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise self._fail()

    def supports_embeddings(self) -> bool:
        return False


def is_placeholder_provider(provider: Any) -> bool:
    """None, the canned FakeProvider or the unconfigured stand-in: not a real LLM.

    A registry-backed platform provider (``RegistryLLMProvider``) is a
    placeholder exactly while the Model Registry has no usable text model —
    checked NOW, so a model added after startup counts at once.
    """
    from app.providers.registry_llm import is_registry_llm

    if is_registry_llm(provider):
        return not provider.has_models()
    return provider is None or isinstance(provider, FakeProvider)


async def aload_tenant_byok_provider(
    tenant_id: str,
    *,
    store: Any = None,
    db_factory: Any = None,
    embed_model: str | None = None,
) -> Any | None:
    """The tenant's own provider, ``None`` when it verifiably has no BYOK config.

    Raises :class:`TenantProviderError` when the config cannot be read (unknown
    is not "none") or exists but is unusable (decrypt / unwrap failure, ...).
    """
    from app.services.llm_config_store import (
        get_llm_config_store,
        get_or_create_worker_llm_config_store,
    )

    store = store or get_llm_config_store() or get_or_create_worker_llm_config_store()
    if store is None:
        raise TenantProviderError(
            "tenant LLM config could not be read (no durable store); not running on the "
            "platform provider in its place"
        )
    try:
        try:
            config = await store.get_config(tenant_id, strict=True)
        except TypeError:  # a store without strict reads (tests / fakes)
            config = await store.get_config(tenant_id)
    except Exception as exc:
        raise TenantProviderError(
            f"tenant LLM config could not be read ({type(exc).__name__}); not running on "
            "the platform provider in its place"
        ) from exc
    return await abuild_tenant_byok_provider(
        config, tenant_id, db_factory=db_factory, embed_model=embed_model
    )


async def abuild_tenant_byok_provider(
    config: dict[str, Any] | None,
    tenant_id: str,
    *,
    db_factory: Any = None,
    embed_model: str | None = None,
) -> Any | None:
    """Unwrap a tenant-vault (``tv1:``) key when needed, then build the provider."""
    if not config:
        return None
    from app.providers.tenant_provider import build_tenant_provider
    from app.providers.tenant_vault import TenantVaultError, prepare_tenant_llm_config

    try:
        if db_factory is None:
            from app.db.session import get_session_factory

            db_factory = get_session_factory()
        prepared = await prepare_tenant_llm_config(dict(config), tenant_id, db_factory)
    except TenantVaultError as exc:  # PROV-15: tenant-vault key unreadable → fail closed
        raise TenantProviderError(f"tenant vault key could not be loaded: {exc}") from exc
    return build_tenant_provider(
        prepared,
        tenant_id=tenant_id,
        embed_model=embed_model or os.getenv("EMBEDDING_MODEL") or None,
    )


def platform_llm_provider() -> Any | None:
    """The platform's provider (deployment cluster, env registry, Model Registry), or None.

    With no env provider this is the registry-backed provider even while the
    Model Registry is empty: long-lived callers (the workflow worker's runner)
    keep it and see a model added later; per call they check
    :func:`is_placeholder_provider`, which is true while nothing is usable.
    """
    from app.scaling.tasks import _worker_deployment_provider

    deployment = _worker_deployment_provider()
    if deployment is not None:
        return deployment
    from app.providers.registry import resolve_provider

    resolved = resolve_provider()
    if not is_placeholder_provider(resolved):
        return resolved
    from app.providers.registry_llm import registry_backed_provider

    return registry_backed_provider()


_UNSET: Any = object()


async def aresolve_tenant_llm_provider(
    tenant_id: str,
    *,
    platform_provider: Any = _UNSET,
    store: Any = None,
    db_factory: Any = None,
) -> Any:
    """Tenant BYOK → platform → :class:`NoLLMProviderConfiguredError`. Never FakeProvider."""
    byok = await aload_tenant_byok_provider(tenant_id, store=store, db_factory=db_factory)
    if byok is not None:
        return byok
    platform = platform_llm_provider() if platform_provider is _UNSET else platform_provider
    if not is_placeholder_provider(platform):
        return platform
    raise NoLLMProviderConfiguredError(no_provider_message(tenant_id))


class TenantLLMProviderResolver:
    """Per-run provider lookup for workflow steps (compiler service ``llm_provider_resolver``).

    ``platform_provider`` is the process's platform provider when the caller
    already has one (the API lifespan); left unset, the worker resolves it.
    """

    def __init__(
        self, *, platform_provider: Any = _UNSET, store: Any = None, db_factory: Any = None
    ) -> None:
        self._platform = platform_provider
        self._store = store
        self._db_factory = db_factory

    async def __call__(self, tenant_id: str) -> Any:
        if not tenant_id:
            raise NoLLMProviderConfiguredError(no_provider_message())
        return await aresolve_tenant_llm_provider(
            tenant_id,
            platform_provider=self._platform,
            store=self._store,
            db_factory=self._db_factory,
        )
