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


def _assert_tenant_base_url_allowed(base_url: str) -> None:
    """A tenant-supplied base_url must be a public host.

    Otherwise every LLM call is an SSRF from the platform into its own network
    (cloud metadata, internal services). Self-hosted deployments whose tenants
    legitimately run models on private hosts list those hosts in
    TENANT_LLM_ALLOWED_PRIVATE_HOSTS (comma-separated hostnames).
    """
    import os
    from urllib.parse import urlparse

    from app.net.ssrf_guard import assert_public_url

    host = (urlparse(base_url).hostname or "").lower()
    allowed = {
        h.strip().lower()
        for h in os.getenv("TENANT_LLM_ALLOWED_PRIVATE_HOSTS", "").split(",")
        if h.strip()
    }
    if host and host in allowed:
        return
    try:
        assert_public_url(base_url, context="tenant_llm_base_url")
    except ValueError as exc:  # SSRFError subclasses ValueError
        raise TenantProviderError(
            f"Tenant LLM base_url is not an allowed public endpoint: {exc}"
        ) from exc


def _normalise(name: str) -> str:
    key = (name or "").strip().lower()
    return _ALIASES.get(key, key)


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
    try:
        from app.providers.vault import get_vault

        api_key = get_vault().decrypt(encrypted_key)
    except Exception as exc:
        raise TenantProviderError(
            "Tenant LLM API key could not be decrypted; re-save it with PUT /tenants/me/llm"
        ) from exc
    if not api_key:
        raise TenantProviderError("Tenant LLM API key is empty")

    model = str(cfg.get("default_model") or cfg.get("model") or "").strip()
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
    return provider


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
        )
    else:
        from app.providers.openai_compatible import OpenAICompatibleProvider

        # Never base_url=None: the OpenAI SDK resolves that to api.openai.com.
        provider = OpenAICompatibleProvider(
            api_key=api_key,
            base_url=base_url or OFFICIAL_BASE_URLS[pname],
            default_model=model,
            embed_model=embed_model,
        )
    return provider
