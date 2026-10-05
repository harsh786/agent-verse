"""BYOK-2 on real Postgres, as the least-privilege application role.

* the API publishes the vault canary; a process with the same key opens it, a
  process with another key gets a ``mismatch`` naming both fingerprints, and a
  rotated API (new key + previous) re-seals it under the new key;
* the tenant LLM config stores the vault key fingerprint next to the ciphertext
  and a rotation keeps it current.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.app_role import AppRoleSpec, ensure_app_role
from app.providers import vault as vault_mod
from app.providers import vault_canary

pytestmark = pytest.mark.integration

KEY_A = "byok2-api-key-" + "a" * 30
KEY_B = "byok2-other-key-" + "b" * 30


@pytest.fixture
async def app_db(pg_url: str) -> AsyncIterator[Any]:
    role, password = f"byok2_app_{secrets.token_hex(4)}", secrets.token_urlsafe(18)
    admin = create_async_engine(pg_url)
    async with admin.begin() as conn:
        await conn.run_sync(ensure_app_role, AppRoleSpec(role=role, password=password))
        await conn.execute(text("DELETE FROM vault_key_canary"))
    url = make_url(pg_url).set(username=role, password=password)
    engine = create_async_engine(url.render_as_string(hide_password=False))
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        await admin.dispose()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    for name in ("AGENTVERSE_VAULT_KEY", "VAULT_PREVIOUS_MASTER_KEYS", "VAULT_MASTER_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    vault_mod._cached_vault.cache_clear()
    yield
    vault_mod._cached_vault.cache_clear()
    vault_canary.reset_last_canary_result()


async def test_canary_publish_check_mismatch_and_rotation(
    app_db: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    fp_a = vault_mod.CredentialVault(KEY_A).fingerprint()
    fp_b = vault_mod.CredentialVault(KEY_B).fingerprint()

    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_A)
    first = await vault_canary.publish_vault_canary(app_db, role="api")
    assert first.ok, first
    assert (await vault_canary.check_vault_canary(app_db, role="worker")).ok
    # Idempotent: a second API start verifies, it does not rewrite.
    assert (await vault_canary.publish_vault_canary(app_db, role="api")).ok

    monkeypatch.setenv("VAULT_MASTER_KEY", KEY_B)
    worker = await vault_canary.check_vault_canary(app_db, role="worker")
    assert worker.status == "mismatch"
    assert fp_a in worker.message and fp_b in worker.message
    assert KEY_A not in worker.message and KEY_B not in worker.message
    # A misconfigured API is unready instead of overwriting the fleet's canary.
    api_b = await vault_canary.publish_vault_canary(app_db, role="api")
    assert api_b.status == "mismatch"
    with pytest.raises(RuntimeError, match="vault key mismatch"):
        await vault_canary.vault_key_health_check(app_db).check()
    async with app_db() as s:
        stored = (await s.execute(text("SELECT fingerprint FROM vault_key_canary"))).scalar_one()
    assert stored == fp_a

    # Rotation: B is the new key, A still decrypts → the API re-seals under B.
    monkeypatch.setenv("VAULT_PREVIOUS_MASTER_KEYS", KEY_A)
    assert (await vault_canary.publish_vault_canary(app_db, role="api")).ok
    async with app_db() as s:
        stored = (await s.execute(text("SELECT fingerprint FROM vault_key_canary"))).scalar_one()
    assert stored == fp_b
    monkeypatch.delenv("VAULT_PREVIOUS_MASTER_KEYS")
    assert (await vault_canary.check_vault_canary(app_db, role="worker")).ok


async def test_llm_config_stores_and_rotation_updates_the_fingerprint(
    app_db: Any, pg_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.providers.vault_rotation import rotate_all_stores
    from app.services.llm_config_store import LLMConfigStore

    tenant = f"t-byok2-{secrets.token_hex(3)}"
    old, new = vault_mod.CredentialVault(KEY_A), vault_mod.CredentialVault(KEY_B)
    admin = create_async_engine(pg_url)
    async with admin.begin() as conn:
        await conn.execute(
            text("INSERT INTO tenants (id, name, email) VALUES (:id, 'T', :e)"),
            {"id": tenant, "e": f"{tenant}@example.com"},
        )
    store = LLMConfigStore(redis_client=None, db_factory=app_db)
    await store.set_config(
        tenant_id=tenant,
        provider="anthropic",
        encrypted_key=old.encrypt("sk-ant-x"),
        model="",
        vault_key_fingerprint=old.fingerprint(),
    )
    cfg = await store.get_config(tenant, strict=True)
    assert cfg is not None and cfg["vault_key_fingerprint"] == old.fingerprint()

    try:
        system = async_sessionmaker(admin, expire_on_commit=False)
        result = await rotate_all_stores(
            old=old,
            new=new,
            rotation_id=f"byok2-{secrets.token_hex(3)}",
            system_db=system,
            tenant_db=app_db,
            redis=None,
        )
    finally:
        await admin.dispose()
    assert result["stores"]["tenant_llm_configs"]["rotated"] >= 1
    cfg = await store.get_config(tenant, strict=True)
    assert cfg is not None
    assert cfg["vault_key_fingerprint"] == new.fingerprint()
    assert new.decrypt(cfg["encrypted_key"]) == "sk-ant-x"
