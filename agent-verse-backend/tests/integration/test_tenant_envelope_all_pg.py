"""TENANT-ENVELOPE-ALL: every tenant secret store uses the tenant's own vault key.

Real Postgres (migrated) + real Redis, through the real stores: connector secrets
(``RedisConnectorSecretStore``), OAuth tokens (``OAuthFlowManager`` /
``oauth_tokens``), ingestion source credentials (``SourceConfigStore``) and
trigger webhook secrets (``ScheduleStore``). Only the LLM key used the tenant key
before; the rest were always sealed with the platform master key.

Checks, per store: values written before the tenant had a key are platform
ciphertext; once it sets a key they still open and are re-wrapped to ``tv1:`` on
read; new writes are ``tv1:``; replacing the key keeps every value readable and
re-wraps it to the new key; a tenant without a key is untouched; and a ``tv1:``
value whose tenant key is gone fails closed without the stored value being
overwritten.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.integration.test_vault_rotate_all_stores_pg import _insert

pytestmark = pytest.mark.integration

MASTER = "tenant-envelope-all-master-key-0123456789"
KEY_A = bytes(range(32))
KEY_B = bytes(range(100, 132))


class _Ctx:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id


@pytest.fixture
async def env(pg_url: str, redis_url: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    import redis.asyncio as aioredis
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.providers.tenant_vault import invalidate_tenant_vault

    monkeypatch.setenv("VAULT_MASTER_KEY", MASTER)
    monkeypatch.delenv("AGENTVERSE_VAULT_KEY", raising=False)
    monkeypatch.delenv("VAULT_PREVIOUS_MASTER_KEYS", raising=False)
    invalidate_tenant_vault()
    tenant, other = "env-t-" + uuid.uuid4().hex[:8], "env-o-" + uuid.uuid4().hex[:8]
    engine = create_async_engine(pg_url)
    async with engine.begin() as conn:
        for t in (tenant, other):
            await _insert(conn, "tenants", {"id": t, "name": t, "email": f"{t}@x.io"})
    db = async_sessionmaker(engine, expire_on_commit=False)
    redis = aioredis.from_url(redis_url, decode_responses=True)
    try:
        yield {"db": db, "redis": redis, "tenant": tenant, "other": other}
    finally:
        invalidate_tenant_vault()
        await redis.aclose()
        await engine.dispose()


async def _scalar(db: Any, sql: str, **params: Any) -> Any:
    from sqlalchemy import text

    async with db() as session, session.begin():
        return (await session.execute(text(sql), params)).scalar_one()


def _stores(env: dict[str, Any]) -> dict[str, Any]:
    from app.ingestion.source_store import SourceConfigStore
    from app.mcp.oauth import OAuthFlowManager
    from app.providers.vault import RedisConnectorSecretStore, get_vault
    from app.triggers.store import ScheduleStore

    oauth = OAuthFlowManager(vault=get_vault())
    oauth._db_session_factory = env["db"]
    return {
        # Fresh instances: nothing is served from an earlier instance's cache.
        "secrets": RedisConnectorSecretStore(
            redis=env["redis"], vault=get_vault(), db_factory=env["db"]
        ),
        "oauth": oauth,
        "sources": SourceConfigStore(env["db"]),
        "schedules": ScheduleStore(db_session_factory=env["db"]),
    }


REF = "vault://connectors/jira/api_key"


async def _seed(env: dict[str, Any], tenant: str) -> dict[str, str]:
    """Write one secret per store for ``tenant`` through the real stores."""
    from app.ingestion.source_config import SourceConfig, SourceFamily
    from app.mcp.oauth import OAuthToken
    from app.triggers.models import TriggerSpec, TriggerType

    s = _stores(env)
    ctx = _Ctx(tenant)
    await s["secrets"].store(REF, f"cs-{tenant}", tenant_ctx=ctx)
    await s["oauth"]._persist_token_to_db(
        tenant, "srv", OAuthToken(access_token=f"at-{tenant}", refresh_token=f"rt-{tenant}")
    )
    source_id = "src-" + uuid.uuid4().hex[:8]
    await s["sources"].create(
        SourceConfig(
            source_id=source_id, tenant_id=tenant, name="jira", family=SourceFamily.WEB,
            source_type="jira",
            connection_config={"base_url": "https://jira", "api_token": f"tok-{tenant}"},
        )
    )
    schedule_id = await s["schedules"].create_async(
        goal_id="g",
        spec=TriggerSpec(
            trigger_type=TriggerType.WEBHOOK,
            webhook_token="wh-" + uuid.uuid4().hex,
            webhook_signature_secret=f"whsec-{tenant}",
        ),
        tenant_ctx=ctx,  # type: ignore[arg-type]
    )
    return {"source_id": source_id, "schedule_id": schedule_id}


async def _raw(env: dict[str, Any], tenant: str, ids: dict[str, str]) -> dict[str, str]:
    """The stored ciphertext of every store (bypassing the stores)."""
    import json

    db = env["db"]
    cfg = await _scalar(
        db, "SELECT connection_config FROM source_configs WHERE id = :i", i=ids["source_id"]
    )
    cfg = json.loads(cfg) if isinstance(cfg, str) else cfg
    return {
        "secret": await env["redis"].get(f"mcp:connector_secrets:{tenant}:jira:api_key"),
        "access": await _scalar(
            db, "SELECT access_token FROM oauth_tokens WHERE tenant_id = :t", t=tenant
        ),
        "refresh": await _scalar(
            db, "SELECT refresh_token FROM oauth_tokens WHERE tenant_id = :t", t=tenant
        ),
        "source": cfg["api_token"],
        "trigger": await _scalar(
            db,
            "SELECT webhook_signature_secret_enc FROM schedules WHERE id = :i",
            i=ids["schedule_id"],
        ),
    }


async def _read_all(env: dict[str, Any], tenant: str, ids: dict[str, str]) -> dict[str, Any]:
    """Read every secret back through fresh stores (this triggers the lazy re-wrap)."""
    s = _stores(env)
    ctx = _Ctx(tenant)
    token = await s["oauth"].aget_token(tenant, "srv")
    source = await s["sources"].get(ids["source_id"], tenant)
    rec = await s["schedules"].get_async(ids["schedule_id"], tenant_ctx=ctx)  # type: ignore[arg-type]
    return {
        "secret": await s["secrets"].resolve(REF, tenant_ctx=ctx),
        "access": token.access_token if token else None,
        "refresh": token.refresh_token if token else None,
        "source": source.connection_config["api_token"] if source else None,
        "trigger": rec["spec"].webhook_signature_secret if rec else None,
    }


def _expected(tenant: str) -> dict[str, str]:
    return {
        "secret": f"cs-{tenant}",
        "access": f"at-{tenant}",
        "refresh": f"rt-{tenant}",
        "source": f"tok-{tenant}",
        "trigger": f"whsec-{tenant}",
    }


def _body(value: str) -> str:
    """The Fernet token inside a stored value (strips enc:v1: / tv1:)."""
    for prefix in ("enc:v1:", "tv1:"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value


def _opens_with_tenant_key_only(value: str, key: bytes) -> bool:
    from app.providers.tenant_vault import TENANT_CIPHER_PREFIX
    from app.providers.vault import CredentialVault, get_vault

    stripped = value[len("enc:v1:"):] if value.startswith("enc:v1:") else value
    if not stripped.startswith(TENANT_CIPHER_PREFIX):
        return False
    CredentialVault.from_byok(key).decrypt(_body(value))
    try:
        get_vault().decrypt(_body(value))
    except Exception:
        return True
    return False


async def test_every_store_seals_with_the_tenant_key_and_rewraps_lazily(env: Any) -> None:
    from app.providers.tenant_vault import is_tenant_encrypted, store_tenant_vault_key

    tenant, other = env["tenant"], env["other"]
    ids = await _seed(env, tenant)
    other_ids = await _seed(env, other)

    # Before the tenant has a key: platform-vault ciphertext everywhere.
    before = await _raw(env, tenant, ids)
    assert not any(is_tenant_encrypted(_body_prefix(v)) for v in before.values())

    await store_tenant_vault_key(env["db"], tenant, KEY_A)

    # Old platform ciphertext still opens, and reading re-wraps it to tv1 (KEY_A).
    assert await _read_all(env, tenant, ids) == _expected(tenant)
    rewrapped = await _raw(env, tenant, ids)
    for name, value in rewrapped.items():
        assert _opens_with_tenant_key_only(value, KEY_A), name

    # New writes are tv1 from the start.
    new_ids = await _seed(env, tenant)
    for name, value in (await _raw(env, tenant, new_ids)).items():
        assert _opens_with_tenant_key_only(value, KEY_A), name

    # A tenant without a key is untouched (platform vault, readable).
    assert await _read_all(env, other, other_ids) == _expected(other)
    assert not any(
        is_tenant_encrypted(_body_prefix(v)) for v in (await _raw(env, other, other_ids)).values()
    )


async def test_key_replacement_keeps_values_readable_and_rewraps_to_the_new_key(
    env: Any,
) -> None:
    from app.providers.tenant_vault import store_tenant_vault_key

    tenant = env["tenant"]
    await store_tenant_vault_key(env["db"], tenant, KEY_A)
    ids = await _seed(env, tenant)  # tv1 under KEY_A
    await store_tenant_vault_key(env["db"], tenant, KEY_B)

    assert await _read_all(env, tenant, ids) == _expected(tenant)
    for name, value in (await _raw(env, tenant, ids)).items():
        assert _opens_with_tenant_key_only(value, KEY_B), name


async def test_tenant_ciphertext_without_its_key_fails_closed_and_is_not_overwritten(
    env: Any,
) -> None:
    from app.providers.tenant_vault import (
        TenantVaultError,
        invalidate_tenant_vault,
        store_tenant_vault_key,
    )
    from app.triggers.store import _UNDECRYPTABLE_SECRET

    tenant = env["tenant"]
    await store_tenant_vault_key(env["db"], tenant, KEY_A)
    ids = await _seed(env, tenant)
    stored = await _raw(env, tenant, ids)
    async with env["db"]() as session, session.begin():
        from sqlalchemy import text

        await session.execute(
            text("DELETE FROM tenant_vault_keys WHERE tenant_id = :t"), {"t": tenant}
        )
    invalidate_tenant_vault()

    s = _stores(env)
    ctx = _Ctx(tenant)
    with pytest.raises(TenantVaultError):
        await s["secrets"].resolve(REF, tenant_ctx=ctx)
    with pytest.raises(TenantVaultError):
        await s["oauth"].aget_token(tenant, "srv")
    source = await s["sources"].get(ids["source_id"], tenant)
    assert source is not None and source.connection_config["api_token"] == ""
    rec = await s["schedules"].get_async(ids["schedule_id"], tenant_ctx=ctx)  # type: ignore[arg-type]
    assert rec is not None and rec["spec"].webhook_signature_secret == _UNDECRYPTABLE_SECRET

    # Nothing was re-sealed with the platform key or blanked in storage.
    assert await _raw(env, tenant, ids) == stored


def _body_prefix(value: str) -> str:
    return value[len("enc:v1:"):] if value.startswith("enc:v1:") else value
