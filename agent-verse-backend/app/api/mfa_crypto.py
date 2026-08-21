"""MFA secret encryption/decryption using Fernet symmetric encryption.

The Fernet key is derived from the SECRET_KEY environment variable via SHA-256
so it is always 32 bytes (required by Fernet).  In production set SECRET_KEY to
a strong random value; the dev default must NEVER reach production.
"""

from __future__ import annotations

import base64
import hashlib
import os


def _get_fernet_key() -> bytes:
    """Derive a URL-safe base64-encoded 32-byte Fernet key from SECRET_KEY."""
    secret = os.getenv("SECRET_KEY", "agentverse-dev-secret-key-change-in-production")
    key_bytes = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(key_bytes)


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a TOTP secret for database storage.

    Returns a Fernet token (URL-safe base64 string).  Falls back to a simple
    base64 encoding suffixed with ``.b64`` when the *cryptography* package is
    absent (development / minimal installs only — NOT suitable for production).
    """
    try:
        from cryptography.fernet import Fernet

        fernet = Fernet(_get_fernet_key())
        return fernet.encrypt(plaintext.encode()).decode()
    except ImportError:
        # Fallback — base64 only (not secure; use as last resort)
        return base64.b64encode(plaintext.encode()).decode() + ".b64"


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a TOTP secret retrieved from the database.

    Handles both the Fernet path and the plain-base64 fallback path
    (identified by the ``.b64`` suffix).

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
