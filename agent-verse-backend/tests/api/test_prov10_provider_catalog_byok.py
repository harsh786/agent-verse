"""PROV-10: GET /tenants/me/providers reports a tenant's own (BYOK) provider as configured."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest


class _Store:
    def __init__(self, cfg: dict[str, Any] | None) -> None:
        self.cfg = cfg

    async def get_config(self, tenant_id: str, *, strict: bool = False) -> Any:
        return self.cfg


def _request(cfg: dict[str, Any] | None) -> Any:
    request = MagicMock()
    request.state = SimpleNamespace(tenant=SimpleNamespace(tenant_id="t1"))
    request.app.state = SimpleNamespace(llm_config_store=_Store(cfg))
    return request


async def test_tenant_byok_provider_is_configured_by_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.tenants import get_provider_catalog

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = await get_provider_catalog(
        _request({"provider": "anthropic", "encrypted_key": "enc", "masked_key": "sk-…1"})
    )
    anthropic = next(p for p in out["providers"] if p["name"] == "anthropic")
    assert anthropic["configured"] is True
    assert anthropic["configured_by"] == "tenant"


async def test_platform_env_provider_is_configured_by_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.tenants import get_provider_catalog

    monkeypatch.setenv("GROQ_API_KEY", "x")
    out = await get_provider_catalog(_request(None))
    groq = next(p for p in out["providers"] if p["name"] == "groq")
    assert groq["configured"] is True and groq["configured_by"] == "platform"
    anthropic = next(p for p in out["providers"] if p["name"] == "anthropic")
    if not anthropic["configured"]:
        assert anthropic["configured_by"] is None


async def test_config_without_a_key_is_not_byok(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api.tenants import get_provider_catalog

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = await get_provider_catalog(_request({"provider": "openai", "encrypted_key": ""}))
    openai = next(p for p in out["providers"] if p["name"] == "openai")
    assert openai["configured"] is False
