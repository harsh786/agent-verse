"""e2e_full: tenant BYOK LLM config is durable in Postgres and tenant-isolated.

It used to live in Redis plus the handling replica's memory; a Redis flush lost
every tenant's key, and other replicas never saw it. Runs on the least-privilege
application role (``E2E_LEAST_PRIVILEGE=1``) so RLS is really enforced.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.e2e_full, pytest.mark.asyncio(loop_scope="session")]


async def _seed_tenant(owner_url: str, tenant_id: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(owner_url)
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO tenants (id, name, email) VALUES (:t, 'llm', :e) "
                "ON CONFLICT (id) DO NOTHING"
            ),
            {"t": tenant_id, "e": f"{tenant_id}@llm.test"},
        )
    await engine.dispose()


async def test_llm_config_survives_cache_loss_and_is_tenant_isolated(
    _backends: tuple[str, str], _migrated_backends: tuple[str, str]
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.services.llm_config_store import LLMConfigStore

    tenant, other = uuid.uuid4().hex, uuid.uuid4().hex
    for t in (tenant, other):
        await _seed_tenant(_backends[0], t)
    engine = create_async_engine(_migrated_backends[0])
    app_factory: Any = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await LLMConfigStore(db_factory=app_factory).set_config(
            tenant, "openai", "ciphertext", "qwen", base_url="http://llm", masked_key="sk-...0000"
        )
        # A different replica / worker with an empty cache reads it from Postgres.
        fresh = LLMConfigStore(db_factory=app_factory)
        cfg = await fresh.get_config(tenant)
        assert cfg is not None
        assert (cfg["provider"], cfg["encrypted_key"], cfg["model"]) == (
            "openai",
            "ciphertext",
            "qwen",
        )
        assert await fresh.get_config(other) is None
        # Update in place (upsert), then delete.
        await fresh.set_config(tenant, "anthropic", "c2", "claude")
        assert (await LLMConfigStore(db_factory=app_factory).get_config(tenant) or {})[
            "provider"
        ] == "anthropic"
        await fresh.delete_config(tenant)
        assert await LLMConfigStore(db_factory=app_factory).get_config(tenant) is None
    finally:
        await engine.dispose()
