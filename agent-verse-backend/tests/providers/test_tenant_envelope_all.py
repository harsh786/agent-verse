"""TENANT-ENVELOPE-ALL unit checks: tenant keyring, lazy re-wrap decision, fail-closed
opening and the source-credential re-encrypt flag. The stores end to end (real
Postgres + Redis) are in tests/integration/test_tenant_envelope_all_pg.py."""

from __future__ import annotations

import base64

import pytest

from app.providers import tenant_vault as tv
from app.providers.vault import CredentialVault, get_vault

KEY_A = bytes(range(32))
KEY_B = bytes(range(50, 82))


def _wrapped(*keys: bytes) -> str:
    return get_vault().encrypt(",".join(base64.b64encode(k).decode() for k in keys))


def test_keyring_encrypts_with_current_key_and_opens_previous_ones() -> None:
    old_value = tv.seal_for_tenant(CredentialVault.from_byok(KEY_A), "s3cret")
    ring = tv._unwrap(_wrapped(KEY_B, KEY_A))
    assert tv.open_for_tenant(ring, old_value) == "s3cret"
    new_value = tv.seal_for_tenant(ring, "s3cret")
    CredentialVault.from_byok(KEY_B).decrypt(new_value[len(tv.TENANT_CIPHER_PREFIX):])
    assert tv.needs_rewrap(ring, old_value) is True
    assert tv.needs_rewrap(ring, new_value) is False


def test_single_key_wrapped_value_from_before_still_unwraps() -> None:
    legacy = get_vault().encrypt(base64.b64encode(KEY_A).decode())
    vault = tv._unwrap(legacy)
    assert vault.decrypt(CredentialVault.from_byok(KEY_A).encrypt("x")) == "x"


def test_malformed_key_material_fails_closed() -> None:
    with pytest.raises(tv.TenantVaultError):
        tv._unwrap(get_vault().encrypt(base64.b64encode(b"short").decode()))


def test_rewrap_decision() -> None:
    ring = CredentialVault.from_byok(KEY_A)
    platform = get_vault().encrypt("x")
    assert tv.needs_rewrap(None, platform) is False  # no tenant key: leave it
    assert tv.needs_rewrap(ring, platform) is True
    assert tv.needs_rewrap(ring, "") is False


def test_tenant_value_without_its_key_never_opens() -> None:
    value = tv.seal_for_tenant(CredentialVault.from_byok(KEY_A), "x")
    with pytest.raises(tv.TenantVaultError):
        tv.open_for_tenant(None, value)
    with pytest.raises(tv.TenantVaultError):
        tv.open_for_tenant(CredentialVault.from_byok(KEY_B), value)


def test_source_config_is_not_reencrypted_when_a_secret_could_not_be_opened() -> None:
    """A blanked (undecryptable) secret must never be written back over the stored one."""
    from app.ingestion.source_secrets import (
        decrypt_connection_config,
        encrypt_connection_config,
    )

    other_tenant = CredentialVault.from_byok(KEY_B)
    stored = {
        **encrypt_connection_config({"api_token": "t"}, CredentialVault.from_byok(KEY_A)),
        "password": "legacy-plaintext",
    }
    cfg, reencrypt = decrypt_connection_config(stored, other_tenant)
    assert cfg["api_token"] == "" and reencrypt is False


def test_source_config_platform_value_is_reported_for_rewrap_and_resealed() -> None:
    from app.ingestion.source_secrets import (
        decrypt_connection_config,
        encrypt_connection_config,
    )

    ring = CredentialVault.from_byok(KEY_A)
    stored = encrypt_connection_config({"api_token": "t", "base_url": "u"})
    cfg, reencrypt = decrypt_connection_config(stored, ring)
    assert cfg == {"api_token": "t", "base_url": "u"} and reencrypt is True
    resealed = encrypt_connection_config(cfg, ring)
    assert resealed["api_token"].startswith("enc:v1:" + tv.TENANT_CIPHER_PREFIX)
    assert decrypt_connection_config(resealed, ring) == (cfg, False)


def test_webhook_secret_round_trip_and_fail_closed_sentinel() -> None:
    from app.triggers.store import (
        _UNDECRYPTABLE_SECRET,
        decrypt_webhook_secret,
        encrypt_webhook_secret,
    )

    ring = CredentialVault.from_byok(KEY_A)
    sealed = encrypt_webhook_secret("whsec", ring)
    assert sealed.startswith(tv.TENANT_CIPHER_PREFIX)
    assert decrypt_webhook_secret(sealed, ring) == "whsec"
    assert decrypt_webhook_secret(sealed) == _UNDECRYPTABLE_SECRET
    assert decrypt_webhook_secret(encrypt_webhook_secret("p")) == "p"
