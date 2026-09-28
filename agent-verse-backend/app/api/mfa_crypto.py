"""MFA secret encryption/decryption using Fernet symmetric encryption.

The Fernet key is derived from the SECRET_KEY environment variable via SHA-256
so it is always 32 bytes (required by Fernet).  In production set SECRET_KEY to
a strong random value; the dev default must NEVER reach production.
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

    Raises:
        ValueError: when decryption fails for any reason.
    """
    try:
        if ciphertext.endswith(".b64"):
            return base64.b64decode(ciphertext[:-4]).decode()
        from cryptography.fernet import Fernet

        fernet = Fernet(_get_fernet_key())
        return fernet.decrypt(ciphertext.encode()).decode()
    except Exception as exc:
        raise ValueError(f"Failed to decrypt MFA secret: {exc}") from exc
