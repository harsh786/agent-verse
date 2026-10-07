"""Per-model endpoint URLs in the Model Registry, and their egress policy.

A registry model may name its own OpenAI-compatible server (``base_url``): a vLLM
model on the LAN (``http://192.168.63.104:30080/v1``), an Ollama ``/v1``
endpoint, an on-prem cluster. Calls for that model go there (see
``app.providers.model_dispatch``).

The URL passes the shared SSRF guard (``app.net.ssrf_guard``): public hosts and,
with ``ALLOW_PRIVATE_NETWORK_ACCESS`` (default on), private / internal hosts and
IPs. Cloud metadata, link-local and 0.0.0.0 are never allowed. With the flag off
the policy is public-only plus the hosts of endpoints already configured in env
(``ONPREM_*``, ``OLLAMA_BASE_URL``, ``EMBEDDING_BASE_URL``,
``RAG_HOSTED_RERANKER_URL``, ``NVIDIA_BASE_URL``).
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from app.net.ssrf_guard import SSRFError, assert_public_url, private_access_networks
from app.observability.logging import get_logger

logger = get_logger(__name__)


class ModelEndpointError(SSRFError):
    """A model endpoint URL that may not be used."""


def _settings() -> Any:
    try:
        from app.core.config import get_settings

        return get_settings()
    except Exception:  # pragma: no cover - defensive
        return None


def _configured_endpoint_hosts() -> list[str]:
    """Hosts of the endpoints the operator configured in env (already trusted)."""
    s = _settings()
    urls = [
        str(getattr(s, attr, "") or "")
        for attr in (
            "onprem_qwen_base_url",
            "onprem_gemma_base_url",
            "onprem_embedding_base_url",
            "onprem_reranker_url",
            "ollama_base_url",
            "embedding_base_url",
            "rag_hosted_reranker_url",
            "nvidia_base_url",
        )
    ]
    urls.append(os.getenv("OLLAMA_BASE_URL", ""))
    hosts = []
    for url in urls:
        host = (urlparse(url.strip()).hostname or "").lower() if url.strip() else ""
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def endpoint_allowed_hosts() -> list[str]:
    """Hosts of env-configured endpoints (always usable, also with the flag off)."""
    return _configured_endpoint_hosts()


def normalize_base_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def check_model_endpoint(url: str) -> str:
    """Validate a model endpoint URL; returns it normalised or raises
    :class:`ModelEndpointError` with a message an operator can act on."""
    base = normalize_base_url(url)
    parsed = urlparse(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ModelEndpointError(
            f"endpoint URL must be http(s)://host[:port][/path], got {url!r}"
        )
    try:
        assert_public_url(
            base,
            allowed_domains=endpoint_allowed_hosts() or None,
            allowed_networks=private_access_networks(),
            context="model endpoint",
        )
    except SSRFError as exc:
        raise ModelEndpointError(
            f"endpoint host {parsed.hostname} is not allowed: {exc} (private hosts need "
            "ALLOW_PRIVATE_NETWORK_ACCESS=true; cloud-metadata / link-local never)"
        ) from exc
    return base


def encrypt_endpoint_api_key(api_key: str) -> str:
    """Vault ciphertext of a model endpoint's credential (what the store keeps).

    Raises :class:`ModelEndpointError` when the credential vault is unusable
    (no master key outside development): the key is never stored in plaintext.
    """
    try:
        from app.providers.vault import get_vault

        return get_vault().encrypt(api_key)
    except Exception as exc:
        raise ModelEndpointError(
            f"cannot store the endpoint credential: the credential vault is unavailable "
            f"({type(exc).__name__}); set AGENTVERSE_VAULT_KEY / VAULT_MASTER_KEY"
        ) from exc


def endpoint_api_key(provider: str, endpoint: Any = None) -> str:
    """The credential sent to a model's own endpoint.

    Precedence: the model's own credential (saved with the registry entry,
    vault-encrypted, decrypted here at call time) > the provider's env key >
    a placeholder (vLLM / Ollama ignore auth; the client needs a value).
    Raises :class:`ModelEndpointError` when a saved credential cannot be
    decrypted (e.g. the vault master key changed) instead of sending nothing.
    """
    from app.core.config import get_provider_env

    secret = str(((getattr(endpoint, "extra", None) or {}).get("api_key_encrypted")) or "")
    if secret:
        try:
            from app.providers.vault import get_vault

            return get_vault().decrypt(secret)
        except Exception as exc:
            raise ModelEndpointError(
                f"the saved credential of {getattr(endpoint, 'model_id', 'this model')} cannot "
                f"be decrypted ({type(exc).__name__}); re-enter it in the Model Registry"
            ) from exc
    env = {
        "nvidia": "NVIDIA_API_KEY",
        "groq": "GROQ_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openai_compatible": "OPENAI_API_KEY",
        "xai": "XAI_API_KEY",
        "gemini": "GOOGLE_API_KEY",
        "google": "GOOGLE_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "onprem": "ONPREM_API_KEY",
    }.get((provider or "").strip().lower())
    return (get_provider_env(env) if env else "") or "EMPTY"


def endpoint_http_client(**kwargs: Any) -> Any:
    """An ``httpx.AsyncClient`` for model-endpoint calls, pinned by the SSRF guard.

    Every connection re-resolves and re-checks the host at CONNECT time
    (``ssrf_guard.public_async_client``) under the same policy as
    :func:`check_model_endpoint` — so a DNS answer that flips to a metadata /
    link-local address after the URL was saved is refused (anti-rebinding).
    Redirects are never followed.
    """
    from app.net.ssrf_guard import public_async_client

    return public_async_client(
        allowed_domains=endpoint_allowed_hosts() or None,
        allowed_networks=private_access_networks(),
        **kwargs,
    )


def onprem_extra_body(provider: str) -> dict[str, Any] | None:
    """vLLM reasoning models: suppress chain-of-thought like the on-prem cluster."""
    if (provider or "").strip().lower() != "onprem":
        return None
    s = _settings()
    if bool(getattr(s, "onprem_disable_thinking", True)):
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return None
