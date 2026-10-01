"""PROV-12: an isolated BYOK run never falls back to the platform key.

``aget_llm_api_key_for_tenant`` read the config non-strictly, so a transient DB
error returned '' (platform key); and when the worker store could not be built,
the config was None and the goal ran on the platform provider.
"""

from __future__ import annotations

from typing import Any

import pytest

import app.services.llm_config_store as lcs


class _FailingStore:
    async def get_config(self, tenant_id: str, *, strict: bool = False) -> Any:
        if strict:
            raise lcs.LLMConfigReadError("db down")
        return None  # the non-strict read hides the failure


async def test_key_read_db_error_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lcs, "get_or_create_worker_llm_config_store", lambda: _FailingStore())
    with pytest.raises(lcs.LLMConfigReadError):
        await lcs.aget_llm_api_key_for_tenant("t1")


async def test_key_read_without_a_store_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lcs, "get_or_create_worker_llm_config_store", lambda: None)
    with pytest.raises(lcs.LLMConfigReadError):
        await lcs.aget_llm_api_key_for_tenant("t1")


def test_worker_provider_without_a_store_fails_the_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.providers.tenant_provider import TenantProviderError
    from app.scaling import tasks

    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr(lcs, "get_or_create_worker_llm_config_store", lambda: None)
    with pytest.raises(TenantProviderError):
        tasks._get_llm_provider("t1")
