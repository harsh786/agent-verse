"""Build a tenant's BYOK (bring-your-own-key) LLM provider.

The single place the API goal path (``GoalService._make_agent_loop_for_tenant``)
and the Celery worker (``app.scaling.tasks._get_llm_provider``) turn a stored
tenant LLM config into a provider. The two paths used to have their own copies,
and both had the same three defects:

* ``groq`` / ``together`` (and ``openai_compatible`` / ``azure`` / ``ollama``)
  configs with an empty ``base_url`` built ``OpenAICompatibleProvider(base_url=None)``,
  which the OpenAI SDK resolves to api.openai.com — the tenant's Groq/Together
  key was sent to OpenAI.
* ``gemini`` / ``nvidia`` / ``openrouter`` (API path also ``openai_compatible``)
  configs matched no branch and silently fell through to the PLATFORM provider:
  the tenant's choice was ignored and the platform paid.
* A vault decrypt failure was swallowed (``except Exception: pass``) and the goal
  ran on the platform provider.

A tenant that configured BYOK now gets its own provider or a
:class:`TenantProviderError` the caller turns into a goal failure — never a
silent fallback to platform spend.

Embedding policy (BYOK)
-----------------------
A tenant's own provider embeds with the **tenant-configured embedding model**
(``embedding_model`` in its LLM config) when one is set — on the tenant's own
endpoint and key. Otherwise it embeds with the **platform Model Registry
embedder** (:func:`app.providers.embedder_factory.process_embedder`), the same
one the knowledge indexes are built with. It never embeds with an env-only model
(``EMBEDDING_MODEL`` used to be forced onto the tenant's endpoint, which may not
serve it) or a provider default. A provider family that cannot embed at all
(Anthropic) always uses the platform embedder.
"""

from __future__ import annotations

from typing import Any

# Official endpoints for vendors reached through the OpenAI-compatible client.
# A config without an explicit base_url is sent here — never to the SDK default
# (api.openai.com) unless the vendor actually IS OpenAI.
OFFICIAL_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
}

# Self-hosted / tenant-specific endpoints: there is no official URL to default to.
REQUIRES_BASE_URL: frozenset[str] = frozenset({"azure", "openai_compatible", "ollama"})

_ALIASES: dict[str, str] = {
    "google": "gemini",
    "nvidia_nim": "nvidia",
    "nim": "nvidia",
}

_DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5.2",
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-2.5-pro",
    "nvidia": "nvidia/llama-3.1-nemotron-70b-instruct",
}

SUPPORTED_TENANT_PROVIDERS: frozenset[str] = frozenset(
    {"anthropic", "gemini", "openrouter", *OFFICIAL_BASE_URLS, *REQUIRES_BASE_URL}
)


class TenantProviderError(RuntimeError):
    """The tenant configured BYOK but its provider cannot be built.

    The message is safe to show the tenant (it never contains the key).
    """


def tenant_circuit_scope(tenant_id: str) -> str:
    """Circuit-breaker scope for a tenant-keyed provider (see circuit_breaker)."""
    return f"tenant:{tenant_id}"


def _allowed_private_hosts() -> list[str]:
    """Operator allowlist of private LLM hosts (TENANT_LLM_ALLOWED_PRIVATE_HOSTS)."""
    import os

    return sorted(
        {
            h.strip().lower()
            for h in os.getenv("TENANT_LLM_ALLOWED_PRIVATE_HOSTS", "").split(",")
            if h.strip()
        }
    )


def _pinned_http_client(base_url: str) -> Any:
    """httpx client for a tenant base_url, pinned to SSRF-checked IPs (SSRF-06).

    The base_url is checked once at build time, but the SDK's own client would
    resolve the name again on every request — a rebinding host would then
    receive the tenant's prompts and API key on an internal address. The
    pinned client re-resolves + checks at every connect and dials the checked
    IP; only the exact host the operator allowlisted may be private.
    """
    from urllib.parse import urlparse

    from app.net.ssrf_guard import private_access_networks, public_async_client

    host = (urlparse(base_url).hostname or "").lower()
    allowed = [host] if host and host in _allowed_private_hosts() else None
    return public_async_client(allowed_domains=allowed, allowed_networks=private_access_networks())


def _assert_tenant_base_url_allowed(base_url: str) -> None:
    """A tenant-supplied base_url must be a public host.

    Otherwise every LLM call is an SSRF from the platform into its own network
    (cloud metadata, internal services). Self-hosted deployments whose tenants
    legitimately run models on private hosts list those hosts in
    TENANT_LLM_ALLOWED_PRIVATE_HOSTS (comma-separated hostnames).
    """
    from urllib.parse import urlparse

    from app.net.ssrf_guard import assert_public_url, private_access_networks

    host = (urlparse(base_url).hostname or "").lower()
    if host and host in _allowed_private_hosts():
        return
    try:
        # ALLOW_PRIVATE_NETWORK_ACCESS (default on): a model endpoint on a
        # private host / IP is allowed; cloud metadata / link-local stay blocked.
        assert_public_url(
            base_url, context="tenant_llm_base_url", allowed_networks=private_access_networks()
        )
    except ValueError as exc:  # SSRFError subclasses ValueError
        raise TenantProviderError(
            f"Tenant LLM base_url is not an allowed public endpoint: {exc}"
        ) from exc


def _normalise(name: str) -> str:
    key = (name or "").strip().lower()
    return _ALIASES.get(key, key)


async def resolve_tenant_byok_provider(app_state: Any, tenant_id: str) -> Any | None:
    """The tenant's own LLM provider (BYOK), ``None`` when it has none configured.

    Order: the pinned ``_llm_provider_override``, else the tenant's config read
    STRICTLY from the durable store. Raises ``LLMConfigReadError`` when the config
    cannot be read and :class:`TenantProviderError` when it exists but is unusable:
    callers must refuse rather than fall back to platform spend.
    """
    from app.services.llm_config_store import get_llm_config_store

    override = getattr(app_state, "_llm_provider_override", None)
    if override is not None:
        return override
    store = getattr(app_state, "llm_config_store", None) or get_llm_config_store()
    if store is None:
        return None
    try:
        cfg = await store.get_config(tenant_id, strict=True)
    except TypeError:  # a store without strict reads (tests/fakes)
        cfg = await store.get_config(tenant_id)
    if not cfg:
        return None
    from app.providers.tenant_vault import TenantVaultError, prepare_tenant_llm_config

    try:
        prepared = await prepare_tenant_llm_config(
            dict(cfg), tenant_id, getattr(app_state, "db_session_factory", None)
        )
    except TenantVaultError as exc:
        raise TenantProviderError(f"Tenant LLM API key could not be unwrapped: {exc}") from exc
    return build_tenant_provider(prepared, tenant_id=tenant_id)


def build_tenant_provider(
    cfg: dict[str, Any] | None,
    *,
    tenant_id: str,
    embed_model: str | None = None,
) -> Any | None:
    """Return the tenant's provider, ``None`` when no BYOK is configured.

    Raises :class:`TenantProviderError` when a config exists but is unusable
    (unknown provider, missing/undecryptable key, missing required base_url or
    model). Callers must fail the goal instead of falling back to the platform.
    """
    if not cfg:
        return None
    raw_name = str(cfg.get("provider") or "")
    encrypted_key = str(cfg.get("encrypted_key") or "")
    if not raw_name and not encrypted_key:
        return None  # an empty record is "not configured", not BYOK

    pname = _normalise(raw_name)
    if pname not in SUPPORTED_TENANT_PROVIDERS:
        raise TenantProviderError(
            f"Tenant LLM provider {raw_name!r} is not supported; "
            "update it with PUT /tenants/me/llm"
        )
    if not encrypted_key:
        raise TenantProviderError(
            f"Tenant LLM provider {pname!r} has no API key; set it with PUT /tenants/me/llm"
        )
    from app.providers.tenant_vault import is_tenant_encrypted

    if is_tenant_encrypted(encrypted_key) and not cfg.get("decrypted_key"):
        # A tenant-vault key (PROV-15) is unwrapped by prepare_tenant_llm_config
        # (async, needs the DB) before this sync builder runs.
        raise TenantProviderError(
            "Tenant LLM API key is encrypted with the tenant vault key, which was not loaded"
        )
    try:
        from app.providers.vault import get_vault

        api_key = str(cfg.get("decrypted_key") or "") or get_vault().decrypt(encrypted_key)
    except Exception as exc:
        # BYOK-2: say WHICH side holds the wrong key (by fingerprint, stored with
        # the ciphertext) instead of an opaque "re-save it" — re-saving from the
        # API never helped a worker that runs with another VAULT_MASTER_KEY.
        from app.providers.vault import explain_decrypt_failure

        reason = explain_decrypt_failure(str(cfg.get("vault_key_fingerprint") or "") or None)
        raise TenantProviderError(f"Tenant LLM API key could not be decrypted: {reason}") from exc
    if not api_key:
        raise TenantProviderError("Tenant LLM API key is empty")

    model = str(cfg.get("default_model") or cfg.get("model") or "").strip()
    # An explicit caller override, else the tenant's own embedding model (policy above).
    embed_model = (embed_model or "").strip() or tenant_embed_model(cfg) or None
    base_url = str(cfg.get("base_url") or "").strip() or None
    if pname in REQUIRES_BASE_URL and base_url is None:
        raise TenantProviderError(f"Tenant LLM provider {pname!r} requires an explicit base_url")
    if base_url is not None:
        _assert_tenant_base_url_allowed(base_url)
    if pname in OFFICIAL_BASE_URLS or pname in REQUIRES_BASE_URL:
        model = model or _DEFAULT_MODELS.get(pname, "")
        if not model:
            raise TenantProviderError(
                f"Tenant LLM provider {pname!r} requires an explicit default_model"
            )

    try:
        provider = _construct(pname, api_key, model, base_url, embed_model)
    except Exception as exc:
        raise TenantProviderError(
            f"Tenant LLM provider {pname!r} could not be initialised ({type(exc).__name__})"
        ) from exc

    # Tenant-keyed provider: its circuit must not be shared with other tenants
    # (a tenant's bad/over-quota key would otherwise open the model's circuit
    # for everyone). See app.providers.circuit_breaker.breaker_key.
    provider._circuit_scope = tenant_circuit_scope(tenant_id)
    provider._byok_tenant_id = tenant_id
    provider._agentverse_provider_type = pname
    return apply_tenant_embedding_policy(provider, pname, embed_model)


def tenant_embed_model(cfg: dict[str, Any] | None) -> str:
    """The embedding model a tenant configured for its own provider ("" = none)."""
    if not cfg:
        return ""
    return str(cfg.get("embedding_model") or cfg.get("embed_model") or "").strip()


def apply_tenant_embedding_policy(provider: Any, pname: str, embed_model: str | None) -> Any:
    """Bind *provider*'s embeddings per the BYOK embedding policy (module docstring).

    The tenant's model when set and the provider family can embed (the
    OpenAI-compatible family reads ``_embed_model_name``; Gemini ``_embed_model``);
    otherwise the platform Model Registry embedder.
    """
    from app.providers.embedder_factory import delegate_embeddings_to_platform

    model = (embed_model or "").strip()
    if model and pname != "anthropic":
        if hasattr(provider, "_embed_model_name"):
            provider._embed_model_name = model
            return provider
        if pname == "gemini" and hasattr(provider, "_embed_model"):
            provider._embed_model = model
            return provider
    return delegate_embeddings_to_platform(provider)


def _construct(
    pname: str, api_key: str, model: str, base_url: str | None, embed_model: str | None
) -> Any:
    provider: Any
    if pname == "anthropic":
        from app.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(api_key=api_key, default_model=model or "claude-opus-4-8")
    elif pname == "gemini":
        from app.providers.gemini_provider import GeminiProvider

        provider = GeminiProvider(api_key=api_key, default_model=model or "gemini-2.5-pro")
    elif pname == "openrouter":
        from app.providers.openrouter_provider import OpenRouterProvider

        provider = OpenRouterProvider(api_key=api_key, default_model=model or None)
    elif pname == "nvidia":
        from app.providers.nvidia_nim_provider import NvidiaNIMProvider

        # Explicit URL: NvidiaNIMProvider would otherwise fall back to the
        # platform's NVIDIA_NIM_BASE_URL (possibly an internal endpoint).
        provider = NvidiaNIMProvider(
            api_key=api_key,
            base_url=base_url or OFFICIAL_BASE_URLS["nvidia"],
            default_model=model or _DEFAULT_MODELS["nvidia"],
            http_client=_pinned_http_client(base_url) if base_url else None,
        )
    else:
        from app.providers.openai_compatible import OpenAICompatibleProvider

        # Never base_url=None: the OpenAI SDK resolves that to api.openai.com.
        provider = OpenAICompatibleProvider(
            api_key=api_key,
            base_url=base_url or OFFICIAL_BASE_URLS[pname],
            default_model=model,
            embed_model=embed_model,
            http_client=_pinned_http_client(base_url) if base_url else None,
        )
    return provider
