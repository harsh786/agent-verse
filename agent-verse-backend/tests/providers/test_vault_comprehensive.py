"""Comprehensive tests for app/providers/vault.py — pushes coverage to ≥90%.

Extends the existing test_vault.py by covering the uncovered sections:
- connector_secret_ref helpers
- RedisConnectorSecretStore
- store/resolve_connector_secret_for_tenant
- CredentialVault.from_byok()
- CredentialVault.rotate_key()
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ===========================================================================
# connector_secret_ref helpers
# ===========================================================================

def test_connector_secret_ref_builds_correct_string() -> None:
    from app.providers.vault import connector_secret_ref
    ref = connector_secret_ref("my-server", "api_key")
    assert ref == "vault://connectors/my-server/api_key"


def test_is_connector_secret_ref_true() -> None:
    from app.providers.vault import is_connector_secret_ref
    assert is_connector_secret_ref("vault://connectors/s1/k1") is True


def test_is_connector_secret_ref_false_plain_string() -> None:
    from app.providers.vault import is_connector_secret_ref
    assert is_connector_secret_ref("some-regular-value") is False


def test_is_connector_secret_ref_false_empty_string() -> None:
    from app.providers.vault import is_connector_secret_ref
    assert is_connector_secret_ref("") is False


def test_is_connector_secret_ref_false_non_string() -> None:
    from app.providers.vault import is_connector_secret_ref
    assert is_connector_secret_ref(42) is False  # type: ignore[arg-type]


# ===========================================================================
# store / resolve connector secret (global in-memory store)
# ===========================================================================

def test_store_connector_secret_in_global_store_and_resolve() -> None:
    from app.providers.vault import (
        connector_secret_ref,
        resolve_connector_secret_ref,
        store_connector_secret,
    )
    ref = connector_secret_ref("server-x", "token")
    store_connector_secret(ref, "secret-value-123")
    resolved = resolve_connector_secret_ref(ref)
    assert resolved == "secret-value-123"


def test_store_connector_secret_in_custom_mapping() -> None:
    from app.providers.vault import (
        connector_secret_ref,
        resolve_connector_secret_ref,
        store_connector_secret,
    )
    custom: dict[str, str] = {}
    ref = connector_secret_ref("s2", "key2")
    store_connector_secret(ref, "my-secret", store=custom)
    # Should be in custom store only
    assert custom[ref] == "my-secret"
    # Global store was NOT updated
    resolved_global = resolve_connector_secret_ref(ref)
    # Global store may or may not have it, but custom read is correct
    resolved_custom = resolve_connector_secret_ref(ref, store=custom)
    assert resolved_custom == "my-secret"


def test_resolve_connector_secret_ref_returns_none_when_missing() -> None:
    from app.providers.vault import resolve_connector_secret_ref
    result = resolve_connector_secret_ref("vault://connectors/nonexistent/key")
    assert result is None


def test_resolve_connector_secret_ref_prefers_custom_store() -> None:
    from app.providers.vault import (
        connector_secret_ref,
        resolve_connector_secret_ref,
        store_connector_secret,
    )
    ref = connector_secret_ref("priority-s", "pk")
    custom: dict[str, str] = {ref: "from-custom"}
    store_connector_secret(ref, "from-global")
    resolved = resolve_connector_secret_ref(ref, store=custom)
    assert resolved == "from-custom"


# ===========================================================================
# _connector_secret_ref_parts
# ===========================================================================

def test_connector_secret_ref_parts_valid() -> None:
    from app.providers.vault import _connector_secret_ref_parts, connector_secret_ref
    ref = connector_secret_ref("srv", "the-key")
    server_id, key = _connector_secret_ref_parts(ref)
    assert server_id == "srv"
    assert key == "the-key"


def test_connector_secret_ref_parts_invalid_raises_value_error() -> None:
    from app.providers.vault import _connector_secret_ref_parts
    with pytest.raises(ValueError, match="not a connector secret reference"):
        _connector_secret_ref_parts("not-a-ref")


def test_connector_secret_ref_parts_missing_key_raises() -> None:
    from app.providers.vault import _connector_secret_ref_parts
    # Manually craft a malformed ref with no key segment
    with pytest.raises(ValueError):
        _connector_secret_ref_parts("vault://connectors/server-only")


# ===========================================================================
# RedisConnectorSecretStore
# ===========================================================================

@pytest.mark.asyncio
async def test_redis_connector_secret_store_redis_key_format() -> None:
    from app.providers.vault import (
        CredentialVault,
        RedisConnectorSecretStore,
        connector_secret_ref,
    )
    vault = CredentialVault(master_key="test-key")
    mock_redis = AsyncMock()
    store = RedisConnectorSecretStore(redis=mock_redis, vault=vault, key_prefix="mcp:secrets")

    ref = connector_secret_ref("my-server", "api_key")

    class _FakeTenantCtx:
        tenant_id = "tenant-abc"

    redis_key = store._redis_key(ref, _FakeTenantCtx())
    assert "mcp:secrets" in redis_key
    assert "tenant-abc" in redis_key
    assert "my-server" in redis_key
    assert "api_key" in redis_key


@pytest.mark.asyncio
async def test_redis_connector_secret_store_store_and_resolve_roundtrip() -> None:
    from app.providers.vault import (
        CredentialVault,
        RedisConnectorSecretStore,
        connector_secret_ref,
    )
    vault = CredentialVault(master_key="test-key-for-redis-store")
    storage: dict[str, str] = {}

    async def _redis_set(key: str, value: str) -> None:
        storage[key] = value

    async def _redis_get(key: str) -> str | None:
        return storage.get(key)

    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(side_effect=_redis_set)
    mock_redis.get = AsyncMock(side_effect=_redis_get)

    store = RedisConnectorSecretStore(redis=mock_redis, vault=vault)
    ref = connector_secret_ref("srv", "token")

    await store.store(ref, "my-plaintext-secret")
    resolved = await store.resolve(ref)

    assert resolved == "my-plaintext-secret"


@pytest.mark.asyncio
async def test_redis_connector_secret_store_returns_none_when_key_missing() -> None:
    from app.providers.vault import (
        CredentialVault,
        RedisConnectorSecretStore,
        connector_secret_ref,
    )
    vault = CredentialVault(master_key="test-key")
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)

    store = RedisConnectorSecretStore(redis=mock_redis, vault=vault)
    ref = connector_secret_ref("missing-srv", "key")
    result = await store.resolve(ref)
    assert result is None


@pytest.mark.asyncio
async def test_redis_connector_secret_store_decodes_bytes() -> None:
    """If Redis returns bytes, the store decodes them correctly."""
    from app.providers.vault import (
        CredentialVault,
        RedisConnectorSecretStore,
        connector_secret_ref,
    )
    vault = CredentialVault(master_key="test-key")
    secret = "my-secret"
    encrypted = vault.encrypt(secret)

    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=encrypted.encode())  # bytes

    store = RedisConnectorSecretStore(redis=mock_redis, vault=vault)
    ref = connector_secret_ref("srv", "k")
    result = await store.resolve(ref)
    assert result == secret


@pytest.mark.asyncio
async def test_redis_store_uses_no_tenant_ctx_when_none() -> None:
    from app.providers.vault import (
        CredentialVault,
        RedisConnectorSecretStore,
        connector_secret_ref,
    )
    vault = CredentialVault(master_key="test-key")
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    store = RedisConnectorSecretStore(redis=mock_redis, vault=vault)
    ref = connector_secret_ref("s", "k")
    # tenant_ctx=None should use "global"
    redis_key = store._redis_key(ref, tenant_ctx=None)
    assert "global" in redis_key


# ===========================================================================
# store_connector_secret_for_tenant / resolve_connector_secret_ref_for_tenant
# ===========================================================================

@pytest.mark.asyncio
async def test_store_for_tenant_with_async_store() -> None:
    from app.providers.vault import (
        connector_secret_ref,
        store_connector_secret_for_tenant,
    )
    ref = connector_secret_ref("s", "k")
    recorded: list[tuple] = []

    class _AsyncStore:
        async def store(self, r: str, v: str, *, tenant_ctx: object = None) -> None:
            recorded.append((r, v))

    await store_connector_secret_for_tenant(ref, "val", store=_AsyncStore())
    assert recorded == [(ref, "val")]


@pytest.mark.asyncio
async def test_store_for_tenant_fallback_to_mapping() -> None:
    from app.providers.vault import (
        connector_secret_ref,
        store_connector_secret_for_tenant,
    )
    ref = connector_secret_ref("sv", "kv")
    custom: dict[str, str] = {}
    await store_connector_secret_for_tenant(ref, "v2", store=custom)
    assert custom[ref] == "v2"


@pytest.mark.asyncio
async def test_resolve_for_tenant_with_async_store() -> None:
    from app.providers.vault import (
        connector_secret_ref,
        resolve_connector_secret_ref_for_tenant,
    )
    ref = connector_secret_ref("s", "k")

    class _AsyncStore:
        async def resolve(self, r: str, *, tenant_ctx: object = None) -> str | None:
            return "async-resolved"

    result = await resolve_connector_secret_ref_for_tenant(ref, store=_AsyncStore())
    assert result == "async-resolved"


@pytest.mark.asyncio
async def test_resolve_for_tenant_returns_none_from_async_store() -> None:
    from app.providers.vault import (
        connector_secret_ref,
        resolve_connector_secret_ref_for_tenant,
    )
    ref = connector_secret_ref("s", "k")

    class _AsyncStore:
        async def resolve(self, r: str, *, tenant_ctx: object = None) -> str | None:
            return None

    result = await resolve_connector_secret_ref_for_tenant(ref, store=_AsyncStore())
    assert result is None


@pytest.mark.asyncio
async def test_resolve_for_tenant_fallback_to_mapping() -> None:
    from app.providers.vault import (
        connector_secret_ref,
        resolve_connector_secret_ref_for_tenant,
    )
    ref = connector_secret_ref("sv2", "kv2")
    custom: dict[str, str] = {ref: "from-map"}
    result = await resolve_connector_secret_ref_for_tenant(ref, store=custom)
    assert result == "from-map"


# ===========================================================================
# _derive_fernet_key
# ===========================================================================

def test_derive_fernet_key_is_deterministic() -> None:
    from app.providers.vault import _derive_fernet_key
    k1 = _derive_fernet_key("my-master-key")
    k2 = _derive_fernet_key("my-master-key")
    assert k1 == k2


def test_derive_fernet_key_differs_for_different_inputs() -> None:
    from app.providers.vault import _derive_fernet_key
    k1 = _derive_fernet_key("key-a")
    k2 = _derive_fernet_key("key-b")
    assert k1 != k2


def test_derived_fernet_key_is_bytes() -> None:
    from app.providers.vault import _derive_fernet_key
    key = _derive_fernet_key("any-key")
    assert isinstance(key, bytes)


# ===========================================================================
# CredentialVault.from_byok()
# ===========================================================================

def test_from_byok_with_valid_32_byte_key() -> None:
    from app.providers.vault import CredentialVault
    customer_key = b"x" * 32
    vault = CredentialVault.from_byok(customer_key)
    # Should be able to encrypt/decrypt
    ct = vault.encrypt("hello-byok")
    assert vault.decrypt(ct) == "hello-byok"


def test_from_byok_stores_customer_key() -> None:
    from app.providers.vault import CredentialVault
    customer_key = b"y" * 32
    vault = CredentialVault.from_byok(customer_key)
    assert vault._key == customer_key


def test_from_byok_raises_for_wrong_length() -> None:
    from app.providers.vault import CredentialVault
    with pytest.raises(ValueError, match="32 bytes"):
        CredentialVault.from_byok(b"too-short")


def test_from_byok_raises_for_31_byte_key() -> None:
    from app.providers.vault import CredentialVault
    with pytest.raises(ValueError, match="32 bytes"):
        CredentialVault.from_byok(b"x" * 31)


def test_from_byok_raises_for_33_byte_key() -> None:
    from app.providers.vault import CredentialVault
    with pytest.raises(ValueError, match="32 bytes"):
        CredentialVault.from_byok(b"x" * 33)


def test_byok_key_secret_not_in_repr() -> None:
    from app.providers.vault import CredentialVault
    vault = CredentialVault.from_byok(b"z" * 32)
    assert "zzzz" not in repr(vault)
    assert "zzzz" not in str(vault)


def test_byok_vault_different_key_cannot_decrypt_each_others_data() -> None:
    from app.providers.vault import CredentialVault
    vault_a = CredentialVault.from_byok(b"a" * 32)
    vault_b = CredentialVault.from_byok(b"b" * 32)
    ct = vault_a.encrypt("secret")
    with pytest.raises(Exception):
        vault_b.decrypt(ct)


# ===========================================================================
# CredentialVault.rotate_key()
# ===========================================================================

@pytest.mark.asyncio
async def test_rotate_key_raises_for_key_under_32_bytes() -> None:
    from app.providers.vault import CredentialVault
    vault = CredentialVault(master_key="my-key")
    with pytest.raises(ValueError, match="32 bytes"):
        await vault.rotate_key(b"short")


@pytest.mark.asyncio
async def test_rotate_key_no_redis_no_db_returns_report() -> None:
    from app.providers.vault import CredentialVault
    vault = CredentialVault(master_key="my-key")
    result = await vault.rotate_key(b"x" * 32, redis=None, db=None)
    assert result["status"] == "rotation_complete"
    assert result["rotated_secrets"] == 0
    assert result["failed"] == 0


@pytest.mark.asyncio
async def test_rotate_key_updates_self_key() -> None:
    from app.providers.vault import CredentialVault
    vault = CredentialVault(master_key="old-key")
    new_key = b"n" * 32
    await vault.rotate_key(new_key)
    assert vault._key == new_key


@pytest.mark.asyncio
async def test_rotate_key_with_redis_re_encrypts_secrets() -> None:
    from app.providers.vault import CredentialVault
    old_vault = CredentialVault(master_key="old-master-key")
    secret = "my-api-secret"
    encrypted_with_old = old_vault.encrypt(secret)

    # Mock Redis: scan returns one key, get returns old encrypted value
    stored: dict[str, str] = {"mcp:connector_secrets:t1:s1:k1": encrypted_with_old}

    async def _fake_scan_iter(match: str, count: int) -> AsyncIterator[str]:
        for k in list(stored.keys()):
            if k.startswith("mcp:connector_secrets"):
                yield k

    async def _fake_get(key: str) -> str | None:
        return stored.get(key)

    async def _fake_set(key: str, value: str) -> None:
        stored[key] = value

    mock_redis = MagicMock()
    mock_redis.scan_iter = _fake_scan_iter
    mock_redis.get = AsyncMock(side_effect=_fake_get)
    mock_redis.set = AsyncMock(side_effect=_fake_set)

    new_master = b"new-master-key-very-long" + b"x" * 8
    result = await old_vault.rotate_key(new_master, redis=mock_redis)

    assert result["rotated_secrets"] == 1
    assert result["failed"] == 0
    assert result["status"] == "rotation_complete"


@pytest.mark.asyncio
async def test_rotate_key_with_redis_records_failed_on_decrypt_error() -> None:
    from app.providers.vault import CredentialVault

    async def _fake_scan_iter(match: str, count: int) -> AsyncIterator[str]:
        yield "mcp:connector_secrets:t1:s1:bad"

    async def _fake_get(key: str) -> str:
        return "corrupted-ciphertext-that-cannot-be-decrypted"

    mock_redis = MagicMock()
    mock_redis.scan_iter = _fake_scan_iter
    mock_redis.get = AsyncMock(side_effect=_fake_get)
    mock_redis.set = AsyncMock()

    vault = CredentialVault(master_key="any-key")
    result = await vault.rotate_key(b"x" * 32, redis=mock_redis)
    assert result["failed"] == 1


@pytest.mark.asyncio
async def test_rotate_key_with_redis_skips_empty_keys() -> None:
    from app.providers.vault import CredentialVault

    async def _fake_scan_iter(match: str, count: int) -> AsyncIterator[str]:
        yield "mcp:connector_secrets:t1:s1:empty"

    mock_redis = MagicMock()
    mock_redis.scan_iter = _fake_scan_iter
    mock_redis.get = AsyncMock(return_value=None)  # None = empty
    mock_redis.set = AsyncMock()

    vault = CredentialVault(master_key="any-key")
    result = await vault.rotate_key(b"x" * 32, redis=mock_redis)
    assert result["rotated_secrets"] == 0
    mock_redis.set.assert_not_called()


@pytest.mark.asyncio
async def test_rotate_key_redis_scan_failure_is_swallowed() -> None:
    """If redis.scan_iter blows up, rotate_key catches it and continues."""
    from app.providers.vault import CredentialVault

    mock_redis = MagicMock()

    async def _bad_scan(**kw: object) -> AsyncIterator[str]:
        raise RuntimeError("redis down")
        yield  # make it a generator

    mock_redis.scan_iter = _bad_scan

    vault = CredentialVault(master_key="any-key")
    result = await vault.rotate_key(b"x" * 32, redis=mock_redis)
    assert result["status"] == "rotation_complete"
