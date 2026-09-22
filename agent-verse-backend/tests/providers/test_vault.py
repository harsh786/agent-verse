"""Tests for the Fernet credential vault."""

from __future__ import annotations

import concurrent.futures

import pytest
from cryptography.fernet import InvalidToken

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
    with pytest.raises(Exception):
        vault2.decrypt(ciphertext)


def test_tampered_ciphertext_raises() -> None:
    vault = _vault()
    ciphertext = vault.encrypt("hello")
    tampered = ciphertext[:-4] + "XXXX"
    with pytest.raises(Exception):
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
    monkeypatch.setenv("ALLOW_DEV_VAULT", "true")  # suppress warning for this test

    vault = get_vault()

    assert vault.decrypt(vault.encrypt("dev-secret")) == "dev-secret"


def test_dev_vault_emits_warning_without_allow_dev(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)
    monkeypatch.delenv("ALLOW_DEV_VAULT", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    import logging

    with caplog.at_level(logging.WARNING, logger="app.providers.vault"):
        from app.providers import vault as vault_module

        vault_module._get_master_key()
    assert any(
        "VAULT SECURITY WARNING" in r.message or "dev-insecure" in r.message.lower()
        for r in caplog.records
    )


def test_production_vault_raises_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VAULT_MASTER_KEY", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    from app.providers import vault as vault_module

    with pytest.raises(RuntimeError, match="VAULT_MASTER_KEY must be set"):
        vault_module._get_master_key()


# ===========================================================================
# Corrupt / truncated ciphertext — must raise a clear, specific error
# ===========================================================================


def test_truncated_ciphertext_raises_invalid_token() -> None:
    """A ciphertext chopped short (e.g. a partial write, a truncated DB column,
    or a network read cut off mid-stream) must raise Fernet's specific
    InvalidToken error — not hang, not silently return garbage, and not raise
    some unrelated low-level decoding exception that obscures the cause."""
    vault = _vault()
    ciphertext = vault.encrypt("a reasonably long secret value to truncate")
    truncated = ciphertext[: len(ciphertext) // 2]
    with pytest.raises(InvalidToken):
        vault.decrypt(truncated)


def test_single_character_ciphertext_raises_invalid_token() -> None:
    """An extreme truncation (almost nothing left) must still fail cleanly."""
    vault = _vault()
    with pytest.raises(InvalidToken):
        vault.decrypt("a")


def test_empty_ciphertext_raises_invalid_token() -> None:
    vault = _vault()
    with pytest.raises(InvalidToken):
        vault.decrypt("")


def test_non_base64_garbage_ciphertext_raises() -> None:
    """A ciphertext that isn't even valid base64 (e.g. corrupted storage,
    wrong column read) must raise cleanly rather than crash with an opaque
    binascii error escaping uncaught."""
    vault = _vault()
    with pytest.raises(Exception):  # deliberately broad: any clean raise is acceptable
        vault.decrypt("not-valid-base64!!!@@@###")


def test_decrypt_failure_does_not_corrupt_vault_state() -> None:
    """A failed decrypt() call (tampered/wrong-key ciphertext) must not leave
    the vault unable to perform subsequent, legitimate encrypt/decrypt calls —
    the Fernet instance must be stateless across calls."""
    vault = _vault()
    good_ciphertext = vault.encrypt("still works after a failure")

    with pytest.raises(InvalidToken):
        vault.decrypt("garbage-ciphertext")

    # The vault must still function normally afterward.
    assert vault.decrypt(good_ciphertext) == "still works after a failure"
    ciphertext2 = vault.encrypt("another secret")
    assert vault.decrypt(ciphertext2) == "another secret"


# ===========================================================================
# Wrong-key decryption — fails safely, no partial-plaintext leakage
# ===========================================================================


def test_wrong_key_decrypt_raises_specific_invalid_token_error() -> None:
    """Decrypting with the wrong master key must raise the specific
    InvalidToken error (AES-GCM authentication failure), not some other
    exception that might suggest the ciphertext was merely malformed."""
    vault_a = _vault("correct-horse-battery-staple")
    vault_b = _vault("a-totally-different-key")
    ciphertext = vault_a.encrypt("top secret api key")
    with pytest.raises(InvalidToken):
        vault_b.decrypt(ciphertext)


def test_wrong_key_decrypt_does_not_leak_partial_plaintext_in_exception() -> None:
    """The failure must not embed any fragment of the real plaintext in the
    exception message/args — an authentication failure must fail closed with
    no information disclosure."""
    vault_a = _vault("key-one")
    vault_b = _vault("key-two")
    secret = "sk-super-sensitive-value-should-never-leak-9f8e7d"
    ciphertext = vault_a.encrypt(secret)
    try:
        vault_b.decrypt(ciphertext)
        pytest.fail("expected InvalidToken")
    except InvalidToken as exc:
        assert secret not in str(exc)
        assert secret not in repr(exc.args)


# ===========================================================================
# Key-rotation scenario (local, no Redis — see test_vault_comprehensive.py for
# the Redis-backed re-encryption path)
# ===========================================================================


@pytest.mark.asyncio
async def test_key_rotation_scenario_new_key_encrypts_and_decrypts() -> None:
    """After rotate_key(), the SAME vault instance must transparently use the
    new key for both new encryptions and decrypting them back — the whole
    point of in-place rotation."""
    vault = _vault("original-master-key")
    await vault.rotate_key(b"r" * 32)

    ciphertext = vault.encrypt("secret encrypted after rotation")
    assert vault.decrypt(ciphertext) == "secret encrypted after rotation"


@pytest.mark.asyncio
async def test_key_rotation_scenario_old_ciphertext_unreadable_without_reencryption() -> None:
    """A secret encrypted BEFORE rotation, that was never re-encrypted (e.g.
    no Redis store was passed to rotate_key, so nothing was migrated), must
    NOT be decryptable with the vault's new in-memory key — this is exactly
    why rotate_key's Redis path re-encrypts every stored secret before
    swapping self._fernet, and this test documents/guards that necessity."""
    vault = _vault("original-master-key")
    old_ciphertext = vault.encrypt("secret encrypted before rotation")

    await vault.rotate_key(b"r" * 32)

    with pytest.raises(InvalidToken):
        vault.decrypt(old_ciphertext)


# ===========================================================================
# Concurrent encrypt/decrypt — must not corrupt shared vault state
# ===========================================================================


def test_concurrent_encrypt_decrypt_round_trips_correctly() -> None:
    """Many threads hammering encrypt()/decrypt() on the SAME vault instance
    concurrently must each get back exactly their own plaintext — no
    cross-thread contamination of ciphertext/plaintext via shared state."""
    vault = _vault("concurrency-test-key")
    plaintexts = [f"secret-value-{i}" for i in range(64)]

    def _round_trip(plaintext: str) -> tuple[str, str]:
        ciphertext = vault.encrypt(plaintext)
        return plaintext, vault.decrypt(ciphertext)

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_round_trip, plaintexts))

    for original, decrypted in results:
        assert original == decrypted


def test_concurrent_encrypt_calls_produce_distinct_ciphertexts_for_same_plaintext() -> None:
    """Concurrent encryption of the IDENTICAL plaintext must still produce
    distinct ciphertexts (random IV per call) and every ciphertext must
    decrypt back correctly — proves there's no shared/reused nonce state
    under concurrent access."""
    vault = _vault("concurrency-nonce-test-key")

    def _encrypt_same(_: int) -> str:
        return vault.encrypt("identical-plaintext-value")

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        ciphertexts = list(pool.map(_encrypt_same, range(32)))

    assert len(set(ciphertexts)) == len(ciphertexts)  # all unique
    for ct in ciphertexts:
        assert vault.decrypt(ct) == "identical-plaintext-value"


def test_concurrent_decrypt_of_shared_ciphertext_is_stable() -> None:
    """Many threads decrypting the SAME ciphertext concurrently must all get
    the identical correct plaintext back, every time."""
    vault = _vault("concurrency-shared-ct-key")
    ciphertext = vault.encrypt("shared-secret-for-concurrent-reads")

    def _decrypt(_: int) -> str:
        return vault.decrypt(ciphertext)

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_decrypt, range(64)))

    assert all(r == "shared-secret-for-concurrent-reads" for r in results)
