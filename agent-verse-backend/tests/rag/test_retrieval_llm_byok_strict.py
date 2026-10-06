"""a08-F195-02: the retrieval LLM never silently falls back to platform spend.

``_resolve_retrieval_llm`` read the tenant's LLM config without ``strict=True``:
a DB read error returned ``None``, which fell to the per-replica
``_llm_configs`` copy or to the platform ``_app_provider`` — a BYOK tenant's
RAG LLM traffic and cost went to the platform vendor. The read is now strict
(the error propagates; the gateway refuses a strategy that needs an LLM), the
per-replica copy is not consulted beside a store, and a key sealed with the
tenant's own vault key is unwrapped as on the goal paths.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.rag.contracts import RAGStrategy
from app.services.llm_config_store import LLMConfigReadError
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext("tenant-byok", PlanTier.PROFESSIONAL, "key-1")


class _DownStore:
    def __init__(self) -> None:
        self.strict: list[bool] = []

    async def get_config(self, tenant_id: str, *, strict: bool = False) -> Any:
        self.strict.append(strict)
        if strict:
            raise LLMConfigReadError("db down")
        return None


class _EmptyStore:
    async def get_config(self, tenant_id: str, *, strict: bool = False) -> Any:
        return None


@pytest.fixture
def app() -> Any:
    from app.main import create_app

    return create_app()


async def test_db_read_error_propagates_instead_of_platform_provider(app: Any) -> None:
    store = _DownStore()
    app.state.llm_config_store = store
    resolver = app.state.retrieval_gateway.dependencies.llm_resolver
    with pytest.raises(LLMConfigReadError):
        await resolver(_CTX, RAGStrategy.FUSION)
    assert store.strict == [True]


async def test_gateway_refuses_a_provider_strategy_on_a_read_error(app: Any) -> None:
    from app.rag.contracts import UnavailableRAGStrategyError

    app.state.llm_config_store = _DownStore()
    gateway = app.state.retrieval_gateway
    with pytest.raises(UnavailableRAGStrategyError):
        await gateway._resolve_llm(RAGStrategy.HYDE, None, _CTX)


async def test_stale_replica_copy_is_ignored_beside_a_store(app: Any) -> None:
    from app.providers.vault import get_vault

    app.state.llm_config_store = _EmptyStore()
    app.state._llm_configs = {
        _CTX.tenant_id: {
            "provider": "anthropic",
            "encrypted_key": get_vault().encrypt("stale-secret"),
            "model": "stale-model",
        }
    }
    resolved = await app.state.retrieval_gateway.dependencies.llm_resolver(
        _CTX, RAGStrategy.FUSION
    )
    # The store says "no BYOK": the stale copy is not used.
    assert resolved is None or resolved.model != "stale-model"


async def test_tenant_vault_sealed_key_is_unwrapped(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.providers import tenant_vault

    calls: list[str] = []

    async def _prepare(cfg: dict[str, Any], tenant_id: str, db: Any) -> dict[str, Any]:
        calls.append(tenant_id)
        return {**cfg, "decrypted_key": "unwrapped-secret"}

    monkeypatch.setattr(tenant_vault, "prepare_tenant_llm_config", _prepare)

    class _Store:
        async def get_config(self, tenant_id: str, *, strict: bool = False) -> Any:
            return {
                "provider": "anthropic",
                "encrypted_key": "tv1:sealed-with-the-tenant-key",
                "model": "claude-tenant-model",
                "base_url": None,
            }

    app.state.llm_config_store = _Store()
    resolved = await app.state.retrieval_gateway.dependencies.llm_resolver(
        _CTX, RAGStrategy.FUSION
    )
    assert calls == [_CTX.tenant_id]
    assert resolved is not None and resolved.model == "claude-tenant-model"
