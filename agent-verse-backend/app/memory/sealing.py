"""Vault sealing of sensitive canonical-memory payloads.

A ``confidential``/``restricted`` memory record exposes only ``[REDACTED]`` as
its ``safe_summary``; its real content is sealed with the platform
:class:`~app.providers.vault.CredentialVault` and stored beside the row, so the
record's ``memory://encrypted/<id>`` reference always resolves to a payload.

The sealed envelope binds the ciphertext to its tenant and memory id: a sealed
value copied onto another row (or another tenant's row) fails to open instead
of leaking. When no vault can be built the write is refused
(:class:`SensitiveMemoryUnavailableError`) — sensitive content is never stored
in plaintext and never left as a dangling reference.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

SEALED_PREFIX = "enc:v1:"


class MemoryPayloadCipher(Protocol):
    def encrypt(self, plaintext: str) -> str: ...
    def decrypt(self, ciphertext: str) -> str: ...


class SensitiveMemoryUnavailableError(RuntimeError):
    """A sensitive memory payload cannot be sealed or opened (no usable vault)."""


_CIPHER_CACHE: dict[tuple[str, ...], MemoryPayloadCipher] = {}


def default_cipher() -> MemoryPayloadCipher:
    """Return the platform vault, cached per key material (PBKDF2 is ~0.3 s).

    Raises :class:`SensitiveMemoryUnavailableError` when the vault cannot be
    built (e.g. production without a master key).
    """
    fingerprint = tuple(
        os.environ.get(name, "")
        for name in (
            "AGENTVERSE_VAULT_KEY",
            "AGENTVERSE_VAULT_KEY_FILE",
            "VAULT_MASTER_KEY",
            "VAULT_MASTER_KEY_FILE",
            "ENVIRONMENT",
        )
    )
    cached = _CIPHER_CACHE.get(fingerprint)
    if cached is not None:
        return cached
    try:
        from app.providers.vault import get_vault

        vault = get_vault()
    except Exception as exc:
        raise SensitiveMemoryUnavailableError(
            "sensitive memory requires the credential vault, which is unavailable: "
            f"{type(exc).__name__}"
        ) from exc
    _CIPHER_CACHE.clear()
    _CIPHER_CACHE[fingerprint] = vault
    return vault


def seal_payload(
    cipher: MemoryPayloadCipher, *, tenant_id: str, memory_id: str, content: str
) -> str:
    envelope = json.dumps(
        {"v": 1, "tenant_id": tenant_id, "memory_id": memory_id, "content": content},
        separators=(",", ":"),
    )
    try:
        return SEALED_PREFIX + cipher.encrypt(envelope)
    except Exception as exc:
        raise SensitiveMemoryUnavailableError(
            f"sensitive memory payload could not be sealed: {type(exc).__name__}"
        ) from exc


def open_payload(
    cipher: MemoryPayloadCipher, *, tenant_id: str, memory_id: str, sealed: str
) -> str:
    if not sealed.startswith(SEALED_PREFIX):
        raise SensitiveMemoryUnavailableError("sensitive memory payload is not sealed")
    try:
        envelope = json.loads(cipher.decrypt(sealed[len(SEALED_PREFIX) :]))
    except Exception as exc:
        raise SensitiveMemoryUnavailableError(
            f"sensitive memory payload could not be opened: {type(exc).__name__}"
        ) from exc
    if envelope.get("tenant_id") != tenant_id or envelope.get("memory_id") != memory_id:
        raise SensitiveMemoryUnavailableError(
            "sensitive memory payload is bound to a different record"
        )
    return str(envelope["content"])


__all__ = [
    "SEALED_PREFIX",
    "MemoryPayloadCipher",
    "SensitiveMemoryUnavailableError",
    "default_cipher",
    "open_payload",
    "seal_payload",
]
