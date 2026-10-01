"""Per-tenant vault keys (BYOK for the secret vault) — envelope encryption (PROV-15).

A tenant may bring its own 32-byte vault key (``POST /tenants/me/vault-key``).
It is stored in ``tenant_vault_keys`` *wrapped* by the platform vault (the
platform master key is the key-encryption key; the tenant key is the data key),
never in plaintext. Secrets written for that tenant afterwards — its LLM API key
(``PUT /tenants/me/llm``) — are encrypted with the tenant key and tagged
``tv1:``; everything else, and tenants without a key, keep the platform vault.

Decrypting a ``tv1:`` value needs the tenant key: if it is missing or cannot be
read the caller gets :class:`TenantVaultError` (fail closed — never a fallback to
another key). Replacing a tenant key re-encrypts the tenant's ``tv1:`` LLM key in
the same transaction, so nothing is orphaned.
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


def is_tenant_encrypted(ciphertext: str) -> bool:
    return isinstance(ciphertext, str) and ciphertext.startswith(TENANT_CIPHER_PREFIX)


def key_fingerprint(key: bytes) -> str:
    """A non-reversible identifier for a key (shown to the tenant, stored)."""
    return hashlib.sha256(b"agentverse-tenant-vault-v1:" + key).hexdigest()[:16]


def _unwrap(wrapped: str) -> CredentialVault:
    try:
        key = base64.b64decode(_vault_mod.get_vault().decrypt(wrapped))
        return CredentialVault.from_byok(key)
    except Exception as exc:
        raise TenantVaultError(f"tenant vault key cannot be unwrapped: {exc}") from exc


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
        raise TenantVaultError(f"tenant vault key could not be read: {exc}") from exc
    return _unwrap(wrapped) if wrapped else None


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

    On replacement the tenant's ``tv1:`` LLM key is re-encrypted with the new key
    in the same transaction.
    """
    if len(key) != 32:
        raise ValueError("Key must be 32 bytes when decoded")
    from sqlalchemy import text

    from app.db.rls import sqlalchemy_rls_context

    new_vault = CredentialVault.from_byok(key)
    wrapped = _vault_mod.get_vault().encrypt(base64.b64encode(key).decode())
    fingerprint = key_fingerprint(key)
    async with (
        db_factory() as session,
        session.begin(),
        sqlalchemy_rls_context(session, tenant_id),
    ):
        old_wrapped = await _read_wrapped(session, tenant_id)
        if old_wrapped:
            old_vault = _unwrap(old_wrapped)
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
                        "UPDATE tenant_llm_configs SET encrypted_key = :k "
                        "WHERE tenant_id = :t"
                    ),
                    {"k": TENANT_CIPHER_PREFIX + new_vault.encrypt(plain), "t": tenant_id},
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
    return fingerprint


async def prepare_tenant_llm_config(
    cfg: dict[str, Any], tenant_id: str, db_factory: Any
) -> dict[str, Any]:
    """A tenant LLM config ready for the (sync) provider builder: a ``tv1:`` key is
    unwrapped here (``decrypted_key``); a platform-vault key is left as it is."""
    encrypted = str(cfg.get("encrypted_key") or "")
    if not is_tenant_encrypted(encrypted):
        return cfg
    return {**cfg, "decrypted_key": await decrypt_tenant_secret(db_factory, tenant_id, encrypted)}
