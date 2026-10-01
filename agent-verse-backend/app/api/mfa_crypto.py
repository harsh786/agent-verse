"""MFA secret encryption/decryption using Fernet symmetric encryption.

The Fernet key is derived from the SECRET_KEY environment variable via SHA-256
so it is always 32 bytes (required by Fernet).  In production set SECRET_KEY to
a strong random value; the dev default must NEVER reach production.

Key ring (MFA-KEY-RING): secrets are sealed with SECRET_KEY and opened with
SECRET_KEY or any key in ``SECRET_KEY_PREVIOUS`` (comma-separated), so
SECRET_KEY can be rotated without locking enrolled users out — changing it used
to make every stored TOTP secret undecryptable. Rotation: deploy with
SECRET_KEY=<new> and SECRET_KEY_PREVIOUS=<old>; a successful verify re-seals
that tenant's secret (:func:`reseal`), and ``agentverse mfa-rotate`` re-seals
all of them; once it reports ``complete`` drop SECRET_KEY_PREVIOUS.
"""

from __future__ import annotations

import base64
import hashlib
import os

_DEV_SECRET = "agentverse-dev-secret-key-change-in-production"


def _get_fernet_key() -> bytes:
    """Derive a URL-safe base64-encoded 32-byte Fernet key from SECRET_KEY.

    In production SECRET_KEY is mandatory: the built-in dev value is public
    (it is in this file), so every TOTP secret encrypted with it is readable by
    anyone with a DB dump.

    Raises:
        RuntimeError: production without a (non-default) SECRET_KEY.
    """
    secret = os.getenv("SECRET_KEY", "")
    if not secret or secret == _DEV_SECRET:
        from app.core.config import get_settings

        if get_settings().environment == "production":
            raise RuntimeError("SECRET_KEY must be set to encrypt MFA secrets in production")
        secret = _DEV_SECRET
    key_bytes = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key_bytes)


def _derive(secret: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())


def _previous_keys() -> list[bytes]:
    """Derived keys of SECRET_KEY_PREVIOUS (decrypt only). The public dev default is
    refused there in production, exactly as it is for SECRET_KEY."""
    raw = os.getenv("SECRET_KEY_PREVIOUS", "")
    secrets = [k.strip() for k in raw.split(",") if k.strip()]
    if _DEV_SECRET in secrets:
        from app.core.config import get_settings

        if get_settings().environment == "production":
            raise RuntimeError("SECRET_KEY_PREVIOUS must not contain the dev secret in production")
    return [_derive(k) for k in secrets]


def _ring() -> object:
    """MultiFernet: encrypts with SECRET_KEY, decrypts with it or a previous key."""
    from cryptography.fernet import Fernet, MultiFernet

    primary = _get_fernet_key()
    return MultiFernet(
        [Fernet(primary), *(Fernet(k) for k in _previous_keys() if k != primary)]
    )


def fingerprint() -> str:
    """Non-reversible id of the current MFA key (rotation checkpoints)."""
    return "mfa-" + hashlib.sha256(b"agentverse-mfa-fp:" + _get_fernet_key()).hexdigest()[:16]


def needs_reseal(ciphertext: str) -> bool:
    """True when a stored secret is not sealed with the current SECRET_KEY (a
    previous key, or a legacy ``.b64`` row) and should be re-sealed."""
    if not ciphertext:
        return False
    if ciphertext.endswith(".b64"):
        return True
    from cryptography.fernet import Fernet

    try:
        Fernet(_get_fernet_key()).decrypt(ciphertext.encode())
    except Exception:
        return True
    return False


def reseal(ciphertext: str) -> str:
    """The secret re-sealed with the current SECRET_KEY (raises ValueError when it
    opens with no key in the ring)."""
    return encrypt_secret(decrypt_secret(ciphertext))


class CurrentKey:
    """The current key alone (``agentverse mfa-rotate``: "already current?")."""

    def encrypt(self, plaintext: str) -> str:
        return encrypt_secret(plaintext)

    def decrypt(self, ciphertext: str) -> str:
        from cryptography.fernet import Fernet

        return Fernet(_get_fernet_key()).decrypt(ciphertext.encode()).decode()


class KeyRing:
    """Every key that may have sealed a stored secret (and legacy ``.b64`` rows)."""

    def decrypt(self, ciphertext: str) -> str:
        return decrypt_secret(ciphertext)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a TOTP secret for database storage (Fernet token).

    There is no fallback: the former "base64 + .b64" path stored TOTP secrets
    in plaintext whenever *cryptography* was missing. ``cryptography`` is a
    hard dependency, so an ImportError now propagates.
    """
    from cryptography.fernet import Fernet

    fernet = Fernet(_get_fernet_key())
    return fernet.encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a TOTP secret retrieved from the database.

    Rows written by the removed plaintext fallback (``.b64`` suffix) are still
    readable so existing enrolments keep working; the next save re-encrypts
    them with Fernet.

    Opens with SECRET_KEY or any SECRET_KEY_PREVIOUS key.

    Raises:
        ValueError: when decryption fails for any reason.
        RuntimeError: a misconfigured key ring in production (never a fallback).
    """
    ring = _ring()  # configuration errors propagate (RuntimeError)
    try:
        if ciphertext.endswith(".b64"):
            return base64.b64decode(ciphertext[:-4]).decode()
        return str(ring.decrypt(ciphertext.encode()).decode())  # type: ignore[attr-defined]
    except Exception as exc:
        raise ValueError(f"Failed to decrypt MFA secret: {exc}") from exc


async def rotate_mfa_secrets(
    *,
    system_db: object,
    tenant_db: object = None,
    dry_run: bool = False,
    batch_size: int = 200,
    progress: object = None,
) -> dict[str, object]:
    """Re-seal every stored TOTP secret (``tenant_mfa.encrypted_secret``) with the
    current SECRET_KEY: batched, per tenant under its RLS context, checkpointed
    (resumable), idempotent, dry-run — the vault-rotate engine over this one store.
    ``previous_keys_retirable`` → SECRET_KEY_PREVIOUS can be dropped."""
    from app.providers.vault_rotation import PgStore, rotate_all_stores

    return await rotate_all_stores(
        old=KeyRing(),
        new=CurrentKey(),
        rotation_id=fingerprint(),
        system_db=system_db,
        tenant_db=tenant_db,
        redis=None,
        dry_run=dry_run,
        batch_size=batch_size,
        progress=progress,  # type: ignore[arg-type]
        pg_stores=(PgStore("mfa_secrets", "tenant_mfa", "tenant_id", ("encrypted_secret",)),),
        record_key_version=False,
    )
