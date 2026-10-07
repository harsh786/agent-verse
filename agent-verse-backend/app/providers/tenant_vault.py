"""Per-tenant vault keys (BYOK for the secret vault) — envelope encryption.

A tenant may bring its own 32-byte vault key (``POST /tenants/me/vault-key``).
It is stored in ``tenant_vault_keys`` *wrapped* by the platform vault (the
platform master key is the key-encryption key; the tenant key is the data key),
never in plaintext. Every tenant secret written afterwards — its LLM API key,
connector secrets, OAuth tokens, ingestion source credentials and trigger
webhook secrets (TENANT-ENVELOPE-ALL) — is encrypted with the tenant key and
tagged ``tv1:``; tenants without a key keep the platform vault. Platform-vault
values of a tenant that has a key are re-wrapped lazily when they are read.

Decrypting a ``tv1:`` value needs the tenant key: if it is missing or cannot be
read the caller gets :class:`TenantVaultError` (fail closed — never a fallback to
another key). Replacing a tenant key keeps the previous keys (wrapped, in the
same row) for decryption only, so no ``tv1:`` value anywhere is orphaned; new
writes and lazy re-wraps use the new key. The tenant's ``tv1:`` LLM key is also
re-encrypted in the replacing transaction. ``agentverse tenant-key-compact``
re-seals whatever is still under a previous key and then drops it
(app/providers/tenant_key_compaction.py).
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any

from app.providers import vault as _vault_mod
from app.providers.vault import CredentialVault

TENANT_CIPHER_PREFIX = "tv1:"


class TenantVaultError(RuntimeError):
    """The tenant vault key is missing or cannot be read / used."""


class TenantVaultUnwrapError(TenantVaultError):
    """The platform vault key cannot unwrap the tenant key (wrong / missing master key)."""


class TenantVaultReadError(TenantVaultError):
    """The tenant key row could not be read (database unavailable) — transient."""


def is_tenant_encrypted(ciphertext: str) -> bool:
    return isinstance(ciphertext, str) and ciphertext.startswith(TENANT_CIPHER_PREFIX)


def key_fingerprint(key: bytes) -> str:
    """A non-reversible identifier for a key (shown to the tenant, stored)."""
    return hashlib.sha256(b"agentverse-tenant-vault-v1:" + key).hexdigest()[:16]


def _unwrap_keys(wrapped: str) -> list[bytes]:
    """The tenant's keys, current first (the wrapped value is ``b64,b64,...``)."""
    try:
        plain = _vault_mod.get_vault().decrypt(wrapped)
        keys = [base64.b64decode(part) for part in plain.split(",") if part]
    except Exception as exc:
        raise TenantVaultUnwrapError(f"tenant vault key cannot be unwrapped: {exc}") from exc
    if not keys or any(len(k) != 32 for k in keys):
        raise TenantVaultError("tenant vault key cannot be unwrapped: malformed key material")
    return keys


def _vault_from_keys(keys: list[bytes]) -> CredentialVault:
    """Encrypts with ``keys[0]``; decrypts with any of them (previous tenant keys)."""
    from cryptography.fernet import Fernet, MultiFernet

    vault = CredentialVault.from_byok(keys[0])
    if len(keys) > 1:
        vault._fernet = MultiFernet([Fernet(base64.urlsafe_b64encode(k)) for k in keys])
        # Opening with the current key only tells a stale tv1 value apart (re-wrap).
        vault._primary_only = CredentialVault.from_byok(keys[0])  # type: ignore[attr-defined]
    return vault


def _unwrap(wrapped: str) -> CredentialVault:
    return _vault_from_keys(_unwrap_keys(wrapped))


async def _read_wrapped(session: Any, tenant_id: str) -> str | None:
    from sqlalchemy import text

    row = (
        await session.execute(
            text("SELECT wrapped_key FROM tenant_vault_keys WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
    ).fetchone()
    return str(row[0]) if row is not None else None


async def load_tenant_vault(db_factory: Any, tenant_id: str) -> CredentialVault | None:
    """The tenant's vault (``None`` when it has no key). Raises on a read error."""
    if db_factory is None:
        return None
    from app.db.rls import sqlalchemy_rls_context

    try:
        async with (
            db_factory() as session,
            session.begin(),
            sqlalchemy_rls_context(session, tenant_id),
        ):
            wrapped = await _read_wrapped(session, tenant_id)
    except Exception as exc:
        raise TenantVaultReadError(f"tenant vault key could not be read: {exc}") from exc
    return _unwrap(wrapped) if wrapped else None


# ── process cache (secrets are read on hot paths: tool calls, webhook checks) ──

_CACHE_TTL_S = 30.0
# Keyed by (session factory, tenant): a factory bound to another database never
# sees a key read through a different one.
_CACHE: dict[tuple[int, str], tuple[float, CredentialVault | None]] = {}


async def ensure_tenant_vault(
    db_factory: Any, tenant_id: str, *, refresh: bool = False
) -> CredentialVault | None:
    """:func:`load_tenant_vault` through a short process cache (raises on read errors)."""
    import time

    if db_factory is None:
        return None
    now = time.monotonic()
    key = (id(db_factory), tenant_id)
    hit = _CACHE.get(key)
    if hit is not None and not refresh and now - hit[0] < _CACHE_TTL_S:
        return hit[1]
    vault = await load_tenant_vault(db_factory, tenant_id)
    _CACHE[key] = (now, vault)
    return vault


def invalidate_tenant_vault(tenant_id: str | None = None) -> None:
    """Drop cached keys of one tenant (all tenants with ``None``)."""
    for key in [k for k in _CACHE if tenant_id is None or k[1] == tenant_id]:
        _CACHE.pop(key, None)


def seal_for_tenant(tenant_vault: CredentialVault | None, plaintext: str) -> str:
    """``tv1:`` ciphertext under the tenant key when it has one, else the platform vault."""
    if tenant_vault is None:
        return _vault_mod.get_vault().encrypt(plaintext)
    return TENANT_CIPHER_PREFIX + tenant_vault.encrypt(plaintext)


def open_for_tenant(tenant_vault: CredentialVault | None, ciphertext: str) -> str:
    """Open either kind; a ``tv1:`` value without the tenant key raises TenantVaultError."""
    if not is_tenant_encrypted(ciphertext):
        return _vault_mod.get_vault().decrypt(ciphertext)
    if tenant_vault is None:
        raise TenantVaultError("value is tenant-vault encrypted but the tenant key is not loaded")
    try:
        return tenant_vault.decrypt(ciphertext[len(TENANT_CIPHER_PREFIX) :])
    except Exception as exc:
        raise TenantVaultError(f"tenant-vault value cannot be decrypted: {exc}") from exc


def needs_rewrap(tenant_vault: CredentialVault | None, ciphertext: str) -> bool:
    """True when a stored value should be re-sealed with the tenant's current key:
    a platform-vault value of a tenant that now has a key, or a ``tv1:`` value
    under one of its previous (replaced) keys."""
    if tenant_vault is None or not ciphertext:
        return False
    if not is_tenant_encrypted(ciphertext):
        return True
    primary_only: CredentialVault | None = getattr(tenant_vault, "_primary_only", None)
    if primary_only is None:
        return False
    try:
        primary_only.decrypt(ciphertext[len(TENANT_CIPHER_PREFIX) :])
    except Exception:
        return True
    return False


async def encrypt_tenant_secret(db_factory: Any, tenant_id: str, plaintext: str) -> str:
    """Encrypt with the tenant key when it has one (``tv1:``), else the platform vault."""
    tenant = await load_tenant_vault(db_factory, tenant_id)
    if tenant is None:
        return _vault_mod.get_vault().encrypt(plaintext)
    return TENANT_CIPHER_PREFIX + tenant.encrypt(plaintext)


async def decrypt_tenant_secret(db_factory: Any, tenant_id: str, ciphertext: str) -> str:
    """Decrypt a value from :func:`encrypt_tenant_secret` (either kind)."""
    if not is_tenant_encrypted(ciphertext):
        return _vault_mod.get_vault().decrypt(ciphertext)
    tenant = await load_tenant_vault(db_factory, tenant_id)
    if tenant is None:
        raise TenantVaultError("value is tenant-vault encrypted but the tenant has no vault key")
    try:
        return tenant.decrypt(ciphertext[len(TENANT_CIPHER_PREFIX) :])
    except Exception as exc:
        raise TenantVaultError(f"tenant-vault value cannot be decrypted: {exc}") from exc


async def store_tenant_vault_key(db_factory: Any, tenant_id: str, key: bytes) -> str:
    """Persist (or replace) the tenant's key, wrapped; returns its fingerprint.

    On replacement the previous keys stay in the row (decrypt only) so ``tv1:``
    values in every store keep opening and are re-wrapped lazily; the tenant's
    ``tv1:`` LLM key is re-encrypted with the new key in the same transaction.
    """
    if len(key) != 32:
        raise ValueError("Key must be 32 bytes when decoded")
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    new_vault = CredentialVault.from_byok(key)
    fingerprint = key_fingerprint(key)
    async with (
        db_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        old_wrapped = await _read_wrapped(session, tenant_id)
        old_keys = _unwrap_keys(old_wrapped) if old_wrapped else []
        keyring = [key, *(k for k in old_keys if k != key)]
        wrapped = _vault_mod.get_vault().encrypt(
            ",".join(base64.b64encode(k).decode() for k in keyring)
        )
        if old_wrapped:
            old_vault = _vault_from_keys(old_keys)
            row = (
                await session.execute(
                    text(
                        "SELECT encrypted_key FROM tenant_llm_configs WHERE tenant_id = :t"
                    ),
                    {"t": tenant_id},
                )
            ).fetchone()
            if row is not None and is_tenant_encrypted(str(row[0])):
                plain = old_vault.decrypt(str(row[0])[len(TENANT_CIPHER_PREFIX) :])
                await session.execute(
                    text(
                        "UPDATE tenant_llm_configs SET encrypted_key = :k, "
                        "vault_key_fingerprint = :fp WHERE tenant_id = :t"
                    ),
                    {
                        "k": TENANT_CIPHER_PREFIX + new_vault.encrypt(plain),
                        # The platform key that wraps the tenant key (BYOK-2).
                        "fp": _vault_mod.get_vault().fingerprint(),
                        "t": tenant_id,
                    },
                )
        await session.execute(
            text(
                "INSERT INTO tenant_vault_keys (tenant_id, wrapped_key, fingerprint) "
                "VALUES (:t, :w, :f) ON CONFLICT (tenant_id) DO UPDATE SET "
                "wrapped_key = EXCLUDED.wrapped_key, fingerprint = EXCLUDED.fingerprint, "
                "updated_at = NOW()"
            ),
            {"t": tenant_id, "w": wrapped, "f": fingerprint},
        )
    invalidate_tenant_vault(tenant_id)
    return fingerprint


async def prepare_tenant_llm_config(
    cfg: dict[str, Any], tenant_id: str, db_factory: Any
) -> dict[str, Any]:
    """A tenant LLM config ready for the (sync) provider builder: a ``tv1:`` key is
    unwrapped here (``decrypted_key``); a platform-vault key is left as it is."""
    encrypted = str(cfg.get("encrypted_key") or "")
    if not is_tenant_encrypted(encrypted):
        return cfg
    try:
        plain = await decrypt_tenant_secret(db_factory, tenant_id, encrypted)
    except TenantVaultUnwrapError as exc:
        # The tenant key is wrapped by the platform master key: name the side
        # whose VAULT_MASTER_KEY differs (BYOK-2).
        reason = _vault_mod.explain_decrypt_failure(
            str(cfg.get("vault_key_fingerprint") or "") or None
        )
        raise TenantVaultUnwrapError(f"{exc}; {reason}") from exc
    return {**cfg, "decrypted_key": plain}
