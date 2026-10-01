"""SSRF-06: a tenant-supplied LLM base_url is dialled through the pinned transport.

The BYOK ``base_url`` was SSRF-checked once when the provider was built; the
OpenAI SDK's own httpx client then resolved the name again on EVERY request, so
a rebinding base_url (public at build time, 127.0.0.1 later) sent the tenant's
prompts and API key to internal addresses. Tenant base_urls now get an
``httpx.AsyncClient`` from ``ssrf_guard.public_async_client``: each connection
is resolved + checked at connect time and dialled to the checked IP.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import httpcore
import pytest

import app.net.ssrf_guard as g
from app.net.ssrf_guard import PinnedNetworkBackend, SSRFError
from app.providers.tenant_provider import build_tenant_provider


def _vault() -> MagicMock:
    v = MagicMock()
    v.decrypt = MagicMock(return_value="tenant-secret-key")
    return v


def _build(cfg: dict[str, Any]) -> Any:
    with patch("app.providers.vault.get_vault", return_value=_vault()):
        return build_tenant_provider(cfg, tenant_id="t-byok")


def _backend(provider: Any) -> Any:
    return provider._client._client._transport._pool._network_backend


@pytest.mark.parametrize("pname", ["openai_compatible", "nvidia", "groq"])
def test_tenant_base_url_gets_the_pinned_transport(
    pname: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(g, "_resolve_host", lambda h: ["93.184.216.34"])
    provider = _build(
        {
            "provider": pname,
            "encrypted_key": "enc",
            "default_model": "m-1",
            "base_url": "https://llm.tenant.example/v1",
        }
    )
    assert isinstance(_backend(provider), PinnedNetworkBackend)
    assert provider._client._client.follow_redirects is False


@pytest.mark.asyncio
async def test_rebinding_base_url_is_refused_at_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter([["93.184.216.34"]])  # build-time check: public; afterwards: loopback
    monkeypatch.setattr(g, "_resolve_host", lambda h: next(answers, ["127.0.0.1"]))
    provider = _build(
        {
            "provider": "openai_compatible",
            "encrypted_key": "enc",
            "default_model": "m-1",
            "base_url": "https://llm.tenant.example/v1",
        }
    )
    dialled: list[str] = []

    async def _connect_tcp(self: Any, host: str, port: int, **kw: Any) -> Any:
        dialled.append(host)
        raise httpcore.ConnectError("dial recorded")

    monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", _connect_tcp)
    with pytest.raises(SSRFError):
        await provider._client._client.get("https://llm.tenant.example/v1/models")
    assert dialled == []


def test_operator_allowlisted_private_host_stays_reachable_but_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TENANT_LLM_ALLOWED_PRIVATE_HOSTS", "10.0.0.5")
    provider = _build(
        {
            "provider": "openai_compatible",
            "encrypted_key": "enc",
            "default_model": "m-1",
            "base_url": "http://10.0.0.5:8000/v1",
        }
    )
    backend = _backend(provider)
    assert isinstance(backend, PinnedNetworkBackend)
    assert backend._allowed_domains == ["10.0.0.5"]
