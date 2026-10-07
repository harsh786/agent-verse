"""Encryption at rest + response masking for ingestion Source credentials.

``SourceConfig.connection_config`` carries whatever the connector needs to log
in — Jira ``api_token``, S3 ``secret_access_key``, a Postgres ``dsn`` with the
password inline, a GCP ``service_account_json``. It used to be written to
``source_configs.connection_config`` verbatim and echoed back on every
``GET/POST/PATCH /sources`` response, so anyone who could read a Source (or the
table, or a backup) got the credentials.

Policy, applied at the persistence boundary (``SourceConfigStore``) and at the
response boundary (``app/api/ingestion.py``):

* **At rest** every *secret-looking* key (see :func:`is_secret_key`) is stored as
  ``enc:v1:<fernet>`` — the vault (``app.providers.vault``) encrypts the
  JSON-encoded value, so non-string secrets (a dict of headers, a service-account
  object) round-trip exactly. Non-secret keys (``base_url``, ``bucket``, ...) stay
  readable so the UI, the due-scan and operators can still see what a Source
  points at.
* **In responses** secret values are replaced by :data:`MASK` and the payload
  gets ``has_credentials``. The API never returns a credential, encrypted or not.
* **On PATCH** a value equal to :data:`MASK` means "unchanged" and keeps the
  stored secret, so a client can round-trip the masked config it was given.
* **Legacy plaintext** rows (written before this module existed) are tolerated
  on read — the value is used as-is — and reported to the caller, which
  re-encrypts the row in the same transaction (read-through migration: no
  offline data migration is needed and the vault key never has to be available
  to Alembic).
* **Tenant envelope key** (TENANT-ENVELOPE-ALL): for a tenant that set its own
  vault key (``tenant_vault_keys``) secrets are stored ``enc:v1:tv1:<fernet>``
  under that key. Platform-vault values of such a tenant are reported for
  re-wrap exactly like legacy plaintext, and a ``tv1:`` value whose tenant key
  is not available is never opened with another key (fail closed).
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from app.observability.logging import get_logger

_log = get_logger(__name__)

ENC_PREFIX = "enc:v1:"
MASK = "********"

# Key names that carry credentials. Matched case-insensitively against the
# whole key; ``dsn`` / ``uri`` / ``connection_string`` are secrets because they
# embed the password (postgresql://user:pw@host, mongodb://user:pw@host).
_SECRET_KEY_RE = re.compile(
    r"(pass(word|wd|phrase)?|secret|token$|tokens$|api[_-]?key|apikey|access[_-]?key"
    r"|account[_-]?key|private[_-]?key|signing[_-]?key|credential|service[_-]?account"
    r"|connection[_-]?string|authorization|cookie|sas[_-]?url"
    r"|^dsn$|^uri$|^auth$|^headers$|^key$)",
    re.IGNORECASE,
)

__all__ = [
    "ENC_PREFIX",
    "MASK",
    "SourceSecretError",
    "decrypt_connection_config",
    "decrypt_connection_config_checked",
    "encrypt_connection_config",
    "is_secret_key",
    "mask_connection_config",
    "merge_masked_update",
]


class SourceSecretError(RuntimeError):
    """A stored secret could not be decrypted (wrong/rotated vault key, tampering)."""


def is_secret_key(key: object) -> bool:
    if not isinstance(key, str) or key.lower().endswith("_id"):
        return False  # identifiers (access_key_id, client_id) are not credentials
    if key.lower().startswith(("max_", "min_", "num_")):
        return False  # max_tokens and friends are limits, not credentials
    return bool(_SECRET_KEY_RE.search(key))


# ``get_vault()`` runs PBKDF2 (480k iterations) on every call — ~0.3 s. A list of
# 100 Sources must not pay that 100 times, so the vault is cached per key
# material (a changed env key — tests, rotation — builds a fresh one).
_VAULT_CACHE: dict[tuple[str, ...], Any] = {}


def _vault() -> Any:
    from app.providers.vault import get_vault

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
    vault = _VAULT_CACHE.get(fingerprint)
    if vault is None:
        vault = get_vault()
        _VAULT_CACHE.clear()
        _VAULT_CACHE[fingerprint] = vault
    return vault


def _is_encrypted(value: object) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def _has_value(value: object) -> bool:
    return value not in (None, "", {}, [])


def _seal(value: Any, tenant_vault: Any) -> str:
    if tenant_vault is None:
        return ENC_PREFIX + _vault().encrypt(json.dumps(value))
    from app.providers.tenant_vault import TENANT_CIPHER_PREFIX

    return ENC_PREFIX + TENANT_CIPHER_PREFIX + tenant_vault.encrypt(json.dumps(value))


def _open(body: str, tenant_vault: Any) -> Any:
    from app.providers.tenant_vault import is_tenant_encrypted, open_for_tenant

    if is_tenant_encrypted(body):
        return json.loads(open_for_tenant(tenant_vault, body))
    return json.loads(_vault().decrypt(body))


def encrypt_connection_config(
    config: dict[str, Any] | None, tenant_vault: Any = None
) -> dict[str, Any]:
    """Return a copy of ``config`` with every secret value vault-encrypted.

    With ``tenant_vault`` (the tenant's own envelope key) secrets are sealed with
    it (``enc:v1:tv1:``). Already-encrypted values are left alone (idempotent),
    empty values stay empty, and nested dicts under non-secret keys are walked.
    """
    out: dict[str, Any] = {}
    for key, value in (config or {}).items():
        if is_secret_key(key):
            if _is_encrypted(value) or not _has_value(value):
                out[key] = value
            else:
                out[key] = _seal(value, tenant_vault)
        elif isinstance(value, dict):
            out[key] = encrypt_connection_config(value, tenant_vault)
        else:
            out[key] = value
    return out


def decrypt_connection_config(
    config: dict[str, Any] | None, tenant_vault: Any = None
) -> tuple[dict[str, Any], bool]:
    """Return ``(plaintext_config, needs_reencrypt)``.

    ``needs_reencrypt`` is True when the caller should re-persist the row
    encrypted: a secret key held a non-empty value that was never encrypted, or
    (with ``tenant_vault``) a value sealed with the platform vault / a replaced
    tenant key (lazy re-wrap). A value that *is* encrypted but cannot be
    decrypted — including a ``tv1:`` value without its tenant key — is dropped to
    ``""`` (the connector then fails authentication — fail closed, never a
    garbage credential) and logged; such a config is never reported for
    re-encryption, so the stored secret is not overwritten with the blank.
    """
    out, reencrypt, failed = _decrypt(config, tenant_vault)
    return out, reencrypt and not failed


def decrypt_connection_config_checked(
    config: dict[str, Any] | None, tenant_vault: Any = None
) -> tuple[dict[str, Any], bool, list[str]]:
    """Like :func:`decrypt_connection_config`, also naming every undecryptable secret.

    Returns ``(plaintext_config, needs_reencrypt, undecryptable)`` where
    ``undecryptable`` lists the dotted key paths (``credentials``,
    ``auth.api_token``) whose stored value is encrypted but could not be opened
    here — a vault key that differs from the one that sealed it (one pod on
    another ``VAULT_MASTER_KEY``), a missing tenant key, tampering. Those values
    are blanked in the config; a connector must refuse to run on them rather than
    fall back to anonymous access or an ambient identity (see
    :attr:`SourceConfig.undecryptable_secrets`).
    """
    out, reencrypt, failed = _decrypt(config, tenant_vault)
    return out, reencrypt and not failed, failed


def _decrypt(
    config: dict[str, Any] | None, tenant_vault: Any, prefix: str = ""
) -> tuple[dict[str, Any], bool, list[str]]:
    from app.providers.tenant_vault import needs_rewrap

    out: dict[str, Any] = {}
    reencrypt = False
    failed: list[str] = []
    for key, value in (config or {}).items():
        if is_secret_key(key) and _is_encrypted(value):
            body = value[len(ENC_PREFIX):]
            try:
                out[key] = _open(body, tenant_vault)
            except Exception as exc:
                _log.error(
                    "source_secret_decrypt_failed", key=key, error=type(exc).__name__
                )
                out[key] = ""
                failed.append(f"{prefix}{key}")
                continue
            reencrypt = reencrypt or needs_rewrap(tenant_vault, body)
        elif is_secret_key(key):
            out[key] = value
            reencrypt = reencrypt or _has_value(value)
        elif isinstance(value, dict):
            out[key], nested_re, nested_failed = _decrypt(value, tenant_vault, f"{prefix}{key}.")
            reencrypt = reencrypt or nested_re
            failed.extend(nested_failed)
        else:
            out[key] = value
    return out, reencrypt, failed


def mask_connection_config(config: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Return ``(masked_config, has_credentials)`` for an API response."""
    out: dict[str, Any] = {}
    has_credentials = False
    for key, value in (config or {}).items():
        if is_secret_key(key):
            if _has_value(value):
                out[key] = MASK
                has_credentials = True
            else:
                out[key] = value
        elif isinstance(value, dict):
            out[key], nested = mask_connection_config(value)
            has_credentials = has_credentials or nested
        else:
            out[key] = value
    return out, has_credentials


def merge_masked_update(
    existing: dict[str, Any] | None, incoming: dict[str, Any]
) -> dict[str, Any]:
    """Resolve a PATCHed ``connection_config`` against the stored one.

    A secret value equal to :data:`MASK` is the client echoing what it was shown,
    i.e. "keep the current secret" — never a literal password of eight stars.
    """
    current = existing or {}
    out: dict[str, Any] = {}
    for key, value in incoming.items():
        if is_secret_key(key) and value == MASK:
            if key in current:
                out[key] = current[key]
            continue
        if isinstance(value, dict) and isinstance(current.get(key), dict):
            out[key] = merge_masked_update(current[key], value)
        else:
            out[key] = value
    return out
