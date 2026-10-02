"""TENANT-KEY-COMPACT on real Postgres + Redis: ``agentverse tenant-key-compact``
re-seals every value still under a replaced tenant key and only then drops that
key from the tenant's keyring; dry run writes nothing; a recent replacement, or
a value no key opens, keeps the previous keys."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from tests.integration.test_tenant_envelope_all_pg import (  # noqa: F401  (env is a fixture)
    _expected,
    _opens_with_tenant_key_only,
    _raw,
    _read_all,
    _seed,
    env,
)

pytestmark = pytest.mark.integration

KEY_A = bytes(range(32))
KEY_B = bytes(range(100, 132))
KEY_UNKNOWN = bytes(range(200, 232))


async def _keyring_size(db: Any, tenant: str) -> int:
    from sqlalchemy import text

    from app.providers.vault import get_vault

    async with db() as s, s.begin():
        wrapped = (
            await s.execute(
                text("SELECT wrapped_key FROM tenant_vault_keys WHERE tenant_id = :t"),
                {"t": tenant},
            )
        ).scalar_one()
    return len(get_vault().decrypt(wrapped).split(","))


async def _seed_redis_copies(ctx: dict[str, Any], tenant: str) -> None:
    """The Redis copies that can carry tv1 values: OAuth copy + LLM-config cache."""
    from app.providers.tenant_vault import seal_for_tenant
    from app.providers.vault import CredentialVault

    old = CredentialVault.from_byok(KEY_A)
    await ctx["redis"].set(
        f"mcp:servers:{tenant}:srv",
        json.dumps({"auth_config": {"_encrypted_access_token": seal_for_tenant(old, "copy")}}),
    )
    await ctx["redis"].set(
        f"llm_config:{tenant}",
        json.dumps({"provider": "openai", "encrypted_key": seal_for_tenant(old, "sk-cache")}),
    )


async def test_compaction_reseals_then_drops_the_replaced_key(env: Any) -> None:  # noqa: F811
    from app.providers.tenant_key_compaction import compact_tenant_keys
    from app.providers.tenant_vault import store_tenant_vault_key

    db, redis, tenant = env["db"], env["redis"], env["tenant"]
    await store_tenant_vault_key(db, tenant, KEY_A)
    ids = await _seed(env, tenant)  # tv1 under KEY_A in every store
    await _seed_redis_copies(env, tenant)
    await store_tenant_vault_key(db, tenant, KEY_B)  # keyring [B, A]
    before = await _raw(env, tenant, ids)

    recent = await compact_tenant_keys(tenant_db=db, redis=redis, tenant_ids=[tenant])
    assert recent["tenants"][0]["status"] == "key_replaced_too_recently", recent

    dry = await compact_tenant_keys(
        tenant_db=db, redis=redis, tenant_ids=[tenant], dry_run=True, min_age_seconds=0
    )
    rep = dry["tenants"][0]
    assert dry["status"] == "dry_run" and rep["status"] == "would_drop_previous_keys"
    assert rep["resealed"] >= 7 and rep["unreadable"] == 0
    assert await _raw(env, tenant, ids) == before
    assert await _keyring_size(db, tenant) == 2

    done = await compact_tenant_keys(
        tenant_db=db, redis=redis, tenant_ids=[tenant], batch_size=1, min_age_seconds=0
    )
    rep = done["tenants"][0]
    assert done["status"] == "complete" and rep["status"] == "compacted"
    assert rep["keys_dropped"] == 1 and await _keyring_size(db, tenant) == 1
    for name, value in (await _raw(env, tenant, ids)).items():
        assert _opens_with_tenant_key_only(value, KEY_B), name
    copy = json.loads(await redis.get(f"mcp:servers:{tenant}:srv"))
    assert _opens_with_tenant_key_only(copy["auth_config"]["_encrypted_access_token"], KEY_B)
    cache = json.loads(await redis.get(f"llm_config:{tenant}"))
    assert _opens_with_tenant_key_only(cache["encrypted_key"], KEY_B)
    assert await _read_all(env, tenant, ids) == _expected(tenant)

    again = await compact_tenant_keys(
        tenant_db=db, redis=redis, tenant_ids=[tenant], min_age_seconds=0
    )
    assert again["tenants"][0]["status"] == "nothing_to_compact"


async def test_a_value_no_key_opens_keeps_the_previous_keys(env: Any) -> None:  # noqa: F811
    from sqlalchemy import text

    from app.providers.tenant_key_compaction import compact_tenant_keys
    from app.providers.tenant_vault import TENANT_CIPHER_PREFIX, store_tenant_vault_key
    from app.providers.vault import CredentialVault

    db, tenant = env["db"], env["tenant"]
    await store_tenant_vault_key(db, tenant, KEY_A)
    await store_tenant_vault_key(db, tenant, KEY_B)
    stray = TENANT_CIPHER_PREFIX + CredentialVault.from_byok(KEY_UNKNOWN).encrypt("x")
    async with db() as s, s.begin():
        await s.execute(
            text(
                "INSERT INTO tenant_llm_configs (tenant_id, provider, encrypted_key) "
                "VALUES (:t, 'openai', :k)"
            ),
            {"t": tenant, "k": stray},
        )

    result = await compact_tenant_keys(tenant_db=db, tenant_ids=[tenant], min_age_seconds=0)
    rep = result["tenants"][0]
    assert result["status"] == "incomplete", result
    assert rep["status"] == "kept_previous_keys" and rep["unreadable"] == 1
    assert await _keyring_size(db, tenant) == 2


def test_wrapped_keyring_format_matches_the_tenant_vault() -> None:
    """The compacted row is the single-key format load_tenant_vault reads."""
    from app.providers.tenant_vault import _unwrap_keys
    from app.providers.vault import get_vault

    assert _unwrap_keys(get_vault().encrypt(base64.b64encode(KEY_B).decode())) == [KEY_B]
