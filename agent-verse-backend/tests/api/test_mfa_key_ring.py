"""MFA-KEY-RING: TOTP secrets are sealed with SECRET_KEY and opened with the key ring
SECRET_KEY + SECRET_KEY_PREVIOUS, so SECRET_KEY can be rotated without locking every
enrolled user out (a changed SECRET_KEY used to make every stored secret
undecryptable: MFA state then read as unavailable, i.e. every login failed).
A successful verify re-seals a secret still under a previous key; ``agentverse
mfa-rotate`` re-seals them all (tests/integration/test_mfa_rotate_pg.py)."""

from __future__ import annotations

import base64
from typing import Any

import pyotp
import pytest

from app.api import mfa_crypto

OLD, NEW = "mfa-old-secret-key-0123456789abcdef", "mfa-new-secret-key-fedcba9876543210"


def _seal_with(monkeypatch: pytest.MonkeyPatch, key: str, plaintext: str) -> str:
    monkeypatch.setenv("SECRET_KEY", key)
    monkeypatch.delenv("SECRET_KEY_PREVIOUS", raising=False)
    return mfa_crypto.encrypt_secret(plaintext)


def test_previous_key_still_opens_and_is_flagged_for_reseal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_ct = _seal_with(monkeypatch, OLD, "JBSWY3DPEHPK3PXP")
    monkeypatch.setenv("SECRET_KEY", NEW)
    monkeypatch.setenv("SECRET_KEY_PREVIOUS", f"unrelated-previous-key-000000000, {OLD}")

    assert mfa_crypto.decrypt_secret(old_ct) == "JBSWY3DPEHPK3PXP"
    assert mfa_crypto.needs_reseal(old_ct) is True
    resealed = mfa_crypto.reseal(old_ct)
    assert mfa_crypto.needs_reseal(resealed) is False

    # Once SECRET_KEY_PREVIOUS is dropped only the re-sealed value opens.
    monkeypatch.delenv("SECRET_KEY_PREVIOUS")
    assert mfa_crypto.decrypt_secret(resealed) == "JBSWY3DPEHPK3PXP"
    with pytest.raises(ValueError):
        mfa_crypto.decrypt_secret(old_ct)


def test_new_secrets_are_sealed_with_the_current_key_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECRET_KEY", NEW)
    monkeypatch.setenv("SECRET_KEY_PREVIOUS", OLD)
    ct = mfa_crypto.encrypt_secret("S")
    monkeypatch.delenv("SECRET_KEY_PREVIOUS")
    assert mfa_crypto.decrypt_secret(ct) == "S"


def test_legacy_b64_rows_are_resealed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", NEW)
    legacy = base64.b64encode(b"LEGACYSECRET").decode() + ".b64"
    assert mfa_crypto.needs_reseal(legacy) is True
    assert mfa_crypto.decrypt_secret(mfa_crypto.reseal(legacy)) == "LEGACYSECRET"


def test_unknown_key_still_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    ct = _seal_with(monkeypatch, OLD, "S")
    monkeypatch.setenv("SECRET_KEY", NEW)
    with pytest.raises(ValueError):
        mfa_crypto.decrypt_secret(ct)


def test_dev_default_is_refused_as_a_previous_key_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("SECRET_KEY", NEW)
    monkeypatch.setenv("SECRET_KEY_PREVIOUS", mfa_crypto._DEV_SECRET)
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError):
            mfa_crypto.decrypt_secret("anything")
    finally:
        monkeypatch.setenv("ENVIRONMENT", "development")
        get_settings.cache_clear()


async def test_successful_verify_reseals_a_secret_under_a_previous_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /auth/mfa/verify with a valid TOTP re-seals the stored secret."""
    from app.api import mfa as mfa_api

    secret = pyotp.random_base32()
    old_ct = _seal_with(monkeypatch, OLD, secret)
    monkeypatch.setenv("SECRET_KEY", NEW)
    monkeypatch.setenv("SECRET_KEY_PREVIOUS", OLD)

    store = mfa_api.MFAStore()
    stored: dict[str, Any] = {"t1": old_ct}

    async def _read(tenant_id: str) -> str | None:
        return stored.get(tenant_id)

    async def _cas(tenant_id: str, old: str, new: str) -> bool:
        if stored.get(tenant_id) != old:
            return False
        stored[tenant_id] = new
        return True

    store._read_encrypted_secret = _read  # type: ignore[method-assign]
    store._swap_encrypted_secret = _cas  # type: ignore[method-assign]
    store._db = object()

    assert await store.reseal_if_needed("t1") is True
    assert mfa_crypto.needs_reseal(stored["t1"]) is False
    assert mfa_crypto.decrypt_secret(stored["t1"]) == secret
    assert await store.reseal_if_needed("t1") is False  # idempotent


@pytest.mark.parametrize(
    ("result", "code"),
    [
        ({"status": "complete", "previous_keys_retirable": True}, 0),
        ({"status": "dry_run", "unreadable": 0}, 0),
        ({"status": "dry_run", "unreadable": 2}, 1),
        ({"status": "failed", "errors": ["x"]}, 1),
    ],
)
def test_cli_mfa_rotate_exit_codes(
    monkeypatch: pytest.MonkeyPatch, result: dict[str, Any], code: int
) -> None:
    from typer.testing import CliRunner

    from app.cli.main import app

    seen: dict[str, Any] = {}

    async def _rotate(**kw: Any) -> dict[str, Any]:
        seen.update(kw)
        return result

    monkeypatch.setattr(mfa_crypto, "rotate_mfa_secrets", _rotate)
    out = CliRunner().invoke(app, ["mfa-rotate", "--dry-run", "--batch-size", "7"])
    assert out.exit_code == code
    assert seen["dry_run"] is True and seen["batch_size"] == 7


def test_verify_endpoint_reseals_only_after_a_valid_code(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.api import mfa as mfa_api
    from tests.api.test_mfa import _HEADERS, _enable_mfa, _make_app

    calls: list[str] = []

    async def _record(tenant_id: str) -> bool:
        calls.append(tenant_id)
        return True

    monkeypatch.setattr(mfa_api._mfa_db_store, "reseal_if_needed", _record)
    secret = _enable_mfa()
    try:
        client = TestClient(_make_app())
        bad = client.post("/auth/mfa/verify", json={"code": "000000"}, headers=_HEADERS)
        assert bad.status_code == 422 and calls == []
        ok = client.post(
            "/auth/mfa/verify", json={"code": pyotp.TOTP(secret).now()}, headers=_HEADERS
        )
        assert ok.status_code == 200 and calls == ["tid-mfa-test"]
    finally:
        mfa_api._mfa_store.pop("tid-mfa-test", None)
        mfa_api._used_totp_codes.pop("tid-mfa-test", None)
        mfa_api._rate_limits.pop("tid-mfa-test", None)
