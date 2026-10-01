"""Tests for P2.7: Vault key rotation and BYOK."""
import pytest

from tests._paths import MIGRATIONS_DIR


def test_vault_rotation_is_an_offline_operation():
    """PROV-13: rotation re-encrypts every store offline (agentverse vault-rotate)."""
    import asyncio

    from app.providers.vault import CredentialVault, rotate_master_key
    assert not hasattr(CredentialVault, "rotate_key")
    assert asyncio.iscoroutinefunction(rotate_master_key)


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


def test_migration_0040_exists():
    import os
    files = os.listdir(MIGRATIONS_DIR)
    assert any("0040" in f for f in files)


def test_migration_0041_exists():
    import os
    files = os.listdir(MIGRATIONS_DIR)
    assert any("0041" in f for f in files)
