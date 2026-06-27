"""Tests for P2.7: Vault key rotation and BYOK."""
import pytest


def test_vault_has_rotate_key_method():
    from app.providers.vault import CredentialVault
    assert hasattr(CredentialVault, "rotate_key"), "CredentialVault must have rotate_key()"
    import asyncio
    assert asyncio.iscoroutinefunction(CredentialVault.rotate_key)


def test_vault_has_from_byok():
    from app.providers.vault import CredentialVault
    assert hasattr(CredentialVault, "from_byok"), "CredentialVault must have from_byok()"


def test_vault_byok_requires_32_bytes():
    from app.providers.vault import CredentialVault
    with pytest.raises((ValueError, Exception)):
        CredentialVault.from_byok(b"too-short")


def test_vault_byok_accepts_32_bytes():
    from app.providers.vault import CredentialVault
    key = b"a" * 32
    vault = CredentialVault.from_byok(key)
    assert vault is not None
    assert vault._key == key


def test_vault_byok_can_encrypt_and_decrypt():
    from app.providers.vault import CredentialVault
    key = b"x" * 32
    vault = CredentialVault.from_byok(key)
    plaintext = "my-secret-api-key"
    ciphertext = vault.encrypt(plaintext)
    assert ciphertext != plaintext
    decrypted = vault.decrypt(ciphertext)
    assert decrypted == plaintext


def test_vault_rotate_key_returns_dict():
    """rotate_key without a DB should still return a valid dict."""
    import asyncio
    from app.providers.vault import CredentialVault
    vault = CredentialVault(master_key="test-master-key")
    result = asyncio.run(vault.rotate_key(new_master_key=b"newkey_32_bytes_long_padding__xx"))
    assert isinstance(result, dict)
    assert "status" in result


def test_vault_rotate_key_rejects_short_key():
    import asyncio
    from app.providers.vault import CredentialVault
    vault = CredentialVault(master_key="test-master-key")
    with pytest.raises(ValueError):
        asyncio.run(vault.rotate_key(new_master_key=b"short"))


def test_migration_0040_exists():
    import os
    files = os.listdir(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend"
        "/app/db/migrations/versions"
    )
    assert any("0040" in f for f in files)


def test_migration_0041_exists():
    import os
    files = os.listdir(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend"
        "/app/db/migrations/versions"
    )
    assert any("0041" in f for f in files)
