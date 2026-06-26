"""Tests for the Fernet credential vault."""

from __future__ import annotations

import pytest

from app.providers.vault import CredentialVault, get_vault


def _vault(master_key: str = "test-master-key-for-unit-tests") -> CredentialVault:
    return CredentialVault(master_key=master_key)


def test_encrypt_and_decrypt_round_trip() -> None:
    vault = _vault()
    plaintext = "sk-secret-api-key-12345"
    ciphertext = vault.encrypt(plaintext)
    assert vault.decrypt(ciphertext) == plaintext


def test_ciphertext_is_different_from_plaintext() -> None:
    vault = _vault()
    plaintext = "my-api-key"
    ciphertext = vault.encrypt(plaintext)
    assert ciphertext != plaintext


def test_same_plaintext_produces_different_ciphertext_each_time() -> None:
    # Fernet uses random IV — same plaintext should not produce same ciphertext
    vault = _vault()
    c1 = vault.encrypt("secret")
    c2 = vault.encrypt("secret")
    assert c1 != c2


def test_wrong_master_key_cannot_decrypt() -> None:
    vault1 = _vault("key-a")
    vault2 = _vault("key-b")
    ciphertext = vault1.encrypt("my-secret")
    with pytest.raises(Exception):  # noqa: B017 — testing Fernet's own error type
        vault2.decrypt(ciphertext)


def test_tampered_ciphertext_raises() -> None:
    vault = _vault()
    ciphertext = vault.encrypt("hello")
    tampered = ciphertext[:-4] + "XXXX"
    with pytest.raises(Exception):  # noqa: B017 — testing Fernet's own error type
        vault.decrypt(tampered)


def test_encrypt_empty_string() -> None:
    vault = _vault()
    assert vault.decrypt(vault.encrypt("")) == ""


def test_vault_secret_never_appears_in_repr() -> None:
    vault = _vault("super-secret")
    # The master key should not leak through __repr__ or __str__
    assert "super-secret" not in repr(vault)
    assert "super-secret" not in str(vault)


def test_get_vault_raises_without_key_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("VAULT_MASTER_KEY_FILE", raising=False)
    monkeypatch.delenv("AGENTVERSE_VAULT_KEY", raising=False)
    monkeypatch.delenv("AGENTVERSE_VAULT_KEY_FILE", raising=False)

    with pytest.raises(RuntimeError, match=r"vault master key.*production"):
        get_vault()


def test_get_vault_rejects_dev_fallback_key_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("AGENTVERSE_VAULT_KEY", "dev-insecure-master-key")
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("VAULT_MASTER_KEY_FILE", raising=False)
    monkeypatch.delenv("AGENTVERSE_VAULT_KEY_FILE", raising=False)

    with pytest.raises(RuntimeError, match="dev-insecure-master-key"):
        get_vault()


def test_get_vault_uses_development_fallback_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("VAULT_MASTER_KEY_FILE", raising=False)
    monkeypatch.delenv("AGENTVERSE_VAULT_KEY", raising=False)
    monkeypatch.delenv("AGENTVERSE_VAULT_KEY_FILE", raising=False)

    vault = get_vault()

    assert vault.decrypt(vault.encrypt("dev-secret")) == "dev-secret"
