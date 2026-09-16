"""Tests for app.api.mfa_crypto — MFA secret Fernet encryption/decryption."""
from __future__ import annotations

import base64

import pytest

from app.api.mfa_crypto import _get_fernet_key, decrypt_secret, encrypt_secret


def test_get_fernet_key_returns_32_byte_urlsafe_b64() -> None:
    key = _get_fernet_key()
    assert isinstance(key, bytes)
    # A valid Fernet key is url-safe base64 encoding of 32 raw bytes.
    decoded = base64.urlsafe_b64decode(key)
    assert len(decoded) == 32


def test_get_fernet_key_deterministic_for_same_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "some-fixed-secret")
    assert _get_fernet_key() == _get_fernet_key()


def test_get_fernet_key_differs_per_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "secret-a")
    key_a = _get_fernet_key()
    monkeypatch.setenv("SECRET_KEY", "secret-b")
    key_b = _get_fernet_key()
    assert key_a != key_b


def test_encrypt_then_decrypt_roundtrip() -> None:
    plaintext = "JBSWY3DPEHPK3PXP"
    token = encrypt_secret(plaintext)
    assert isinstance(token, str)
    assert token != plaintext

    decrypted = decrypt_secret(token)
    assert decrypted == plaintext


def test_encrypt_uses_fernet_when_cryptography_available() -> None:
    pytest.importorskip("cryptography")
    token = encrypt_secret("my-totp-secret")
    assert not token.endswith(".b64")


def test_encrypt_falls_back_to_base64_without_cryptography(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "cryptography.fernet" or name.startswith("cryptography"):
            raise ImportError("cryptography not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    token = encrypt_secret("plain-secret")
    assert token.endswith(".b64")
    assert base64.b64decode(token[:-4]).decode() == "plain-secret"


def test_decrypt_handles_base64_fallback_path() -> None:
    token = base64.b64encode(b"fallback-secret").decode() + ".b64"
    assert decrypt_secret(token) == "fallback-secret"


def test_decrypt_invalid_ciphertext_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Failed to decrypt MFA secret"):
        decrypt_secret("not-a-valid-fernet-token")


def test_decrypt_tampered_fernet_token_raises() -> None:
    token = encrypt_secret("original-secret")
    if token.endswith(".b64"):
        pytest.skip("cryptography not installed — Fernet path unavailable")
    tampered = token[:-4] + ("A" if token[-4] != "A" else "B")
    with pytest.raises(ValueError):
        decrypt_secret(tampered)
