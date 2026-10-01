"""Credential vault — AES-256-GCM encryption via Fernet.

All LLM API keys and MCP connector credentials pass through this vault before
touching Redis or PostgreSQL. The master key is read from the environment via
``read_secret()`` (``VAULT_MASTER_KEY`` or ``VAULT_MASTER_KEY_FILE``).

Key derivation: PBKDF2-HMAC-SHA256, 480 000 iterations (NIST SP 800-132, 2024).
"""

from __future__ import annotations

import base64
import functools
import hashlib
import inspect
import logging as _logging
import os
from collections.abc import MutableMapping
from typing import Any

from cryptography.fernet import Fernet, MultiFernet

_vault_log = _logging.getLogger(__name__)

_DEV_INSECURE_MASTER_KEY = "dev-insecure-master-key"
_CONNECTOR_SECRET_PREFIX = "vault://connectors/"


class ConnectorSecretUnavailableError(LookupError):
    """A connector secret cannot be stored / resolved (no store, or unknown ref).

    There is deliberately no per-process fallback: a secret kept in one
    replica's memory cannot be resolved by another, and connector auth used to
    send an empty value instead of failing.
    """


def _get_master_key() -> str:
    """Return the vault master key from the environment.

    - Raises ``RuntimeError`` if ``ENVIRONMENT=production`` and no key is set.
    - Logs a prominent WARNING in development when the insecure dev key is used
      without ``ALLOW_DEV_VAULT=true`` acknowledgment.
    """
    key = os.getenv("VAULT_MASTER_KEY", "")
    if key:
        return key
    env = os.getenv("ENVIRONMENT", "development").lower()
    if env == "production":
        raise RuntimeError(
            "VAULT_MASTER_KEY must be set in production. Set VAULT_MASTER_KEY environment variable."
        )
    allow_dev = os.getenv("ALLOW_DEV_VAULT", "").lower() in ("true", "1", "yes")
    if not allow_dev:
        _vault_log.warning(
            "VAULT SECURITY WARNING: Using dev-insecure-master-key because "
            "VAULT_MASTER_KEY is not set. Encrypted credentials are NOT secure. "
            "Set ALLOW_DEV_VAULT=true to suppress this warning in development."
        )
    return _DEV_INSECURE_MASTER_KEY


def connector_secret_ref(server_id: str, key: str) -> str:
    """Build the persisted secret reference for a connector auth_config key."""
    return f"{_CONNECTOR_SECRET_PREFIX}{server_id}/{key}"


def is_connector_secret_ref(value: object) -> bool:
    """Return True when *value* is a connector secret reference."""
    return isinstance(value, str) and value.startswith(_CONNECTOR_SECRET_PREFIX)


def store_connector_secret(
    ref: str,
    value: str,
    *,
    store: MutableMapping[str, str] | None = None,
) -> None:
    """Store a connector secret in the provided store (required)."""
    if store is None:
        raise ConnectorSecretUnavailableError(
            "no connector secret store is configured; refusing to keep the secret in "
            "process memory"
        )
    store[ref] = value


def resolve_connector_secret_ref(
    ref: str,
    *,
    store: MutableMapping[str, str] | None = None,
) -> str:
    """Resolve a connector secret reference; raises when it cannot be resolved."""
    if store is not None and ref in store:
        return store[ref]
    raise ConnectorSecretUnavailableError(f"connector secret {ref!r} could not be resolved")


def _connector_secret_ref_parts(ref: str) -> tuple[str, str]:
    if not is_connector_secret_ref(ref):
        raise ValueError("not a connector secret reference")
    remainder = ref[len(_CONNECTOR_SECRET_PREFIX) :]
    server_id, separator, key = remainder.partition("/")
    if not server_id or not separator or not key:
        raise ValueError("invalid connector secret reference")
    return server_id, key


class RedisConnectorSecretStore:
    """Encrypted Redis-backed connector secret store scoped by tenant and server."""

    production_safe = True

    def __init__(
        self,
        *,
        redis: Any,
        vault: CredentialVault,
        key_prefix: str = "mcp:connector_secrets",
        db_factory: Any = None,
    ) -> None:
        self._redis = redis
        self._vault = vault
        self._key_prefix = key_prefix
        # With a DB, a tenant that set its own vault key (tenant_vault_keys) gets
        # its connector secrets sealed with it (TENANT-ENVELOPE-ALL).
        self._db_factory = db_factory

    def _redis_key(self, ref: str, tenant_ctx: Any = None) -> str:
        # Handle both "vault://connectors/<server>/<key>" and
        # "secret://connector/<server>/<key>" formats.
        if ref.startswith("secret://connector/"):
            remainder = ref[len("secret://connector/") :]
        elif is_connector_secret_ref(ref):
            remainder = ref[len(_CONNECTOR_SECRET_PREFIX) :]
        else:
            raise ValueError(f"Unrecognized secret ref format: {ref!r}")
        server_id, separator, key = remainder.partition("/")
        if not server_id or not separator or not key:
            raise ValueError(f"Invalid secret ref format: {ref!r}")
        tenant_id = getattr(tenant_ctx, "tenant_id", "global") or "global"
        return f"{self._key_prefix}:{tenant_id}:{server_id}:{key}"

    async def _tenant_vault(self, tenant_ctx: Any) -> CredentialVault | None:
        tenant_id = getattr(tenant_ctx, "tenant_id", None)
        if self._db_factory is None or not tenant_id:
            return None
        from app.providers.tenant_vault import ensure_tenant_vault

        return await ensure_tenant_vault(self._db_factory, str(tenant_id))

    async def store(self, ref: str, value: str, *, tenant_ctx: Any = None) -> None:
        tenant_vault = await self._tenant_vault(tenant_ctx)
        if tenant_vault is not None:
            from app.providers.tenant_vault import seal_for_tenant

            encrypted = seal_for_tenant(tenant_vault, value)
        else:
            encrypted = self._vault.encrypt(value)
        await self._redis.set(self._redis_key(ref, tenant_ctx), encrypted)

    async def resolve(self, ref: str, *, tenant_ctx: Any = None) -> str | None:
        key = self._redis_key(ref, tenant_ctx)
        raw = await self._redis.get(key)
        if raw is None:
            return None
        encrypted = raw.decode() if isinstance(raw, bytes) else str(raw)
        from app.providers.tenant_vault import (
            is_tenant_encrypted,
            needs_rewrap,
            open_for_tenant,
            seal_for_tenant,
        )

        tenant_vault: CredentialVault | None
        if is_tenant_encrypted(encrypted):
            # Needs the tenant key: missing / unreadable raises (fail closed).
            tenant_vault = await self._tenant_vault(tenant_ctx)
            plaintext = open_for_tenant(tenant_vault, encrypted)
        else:
            plaintext = self._vault.decrypt(encrypted)
            try:  # the tenant key is only needed to re-wrap here: best effort
                tenant_vault = await self._tenant_vault(tenant_ctx)
            except Exception as exc:
                _vault_log.warning("connector_secret_rewrap_skipped: %s", type(exc).__name__)
                tenant_vault = None
        if needs_rewrap(tenant_vault, encrypted):
            # Lazy re-wrap: a platform-vault (or replaced-tenant-key) secret of a
            # tenant that has its own key is re-sealed with the current key.
            await self._redis.set(key, seal_for_tenant(tenant_vault, plaintext))
        return plaintext


async def store_connector_secret_for_tenant(
    ref: str,
    value: str,
    *,
    store: Any = None,
    tenant_ctx: Any = None,
) -> None:
    """Store a connector secret in a tenant-aware store (or an explicit mapping)."""
    if store is not None and hasattr(store, "store"):
        result = store.store(ref, value, tenant_ctx=tenant_ctx)
        if inspect.isawaitable(result):
            await result
        return
    store_connector_secret(ref, value, store=store)


async def resolve_connector_secret_ref_for_tenant(
    ref: str,
    *,
    store: Any = None,
    tenant_ctx: Any = None,
) -> str:
    """Resolve a connector secret from a tenant-aware store; raises when missing."""
    if store is not None and hasattr(store, "resolve"):
        result = store.resolve(ref, tenant_ctx=tenant_ctx)
        if inspect.isawaitable(result):
            result = await result
        if result is None:
            raise ConnectorSecretUnavailableError(
                f"connector secret {ref!r} could not be resolved"
            )
        return str(result)
    return resolve_connector_secret_ref(ref, store=store)


def _derive_fernet_key(master_key: str) -> bytes:
    """Derive a 32-byte key from *master_key* using PBKDF2 with a fixed salt.

    The salt is deterministic (derived from the constant string "agentverse-vault-v1")
    so the same master key always produces the same Fernet key.
    """
    # Fixed salt — this is acceptable because the master_key is already a secret;
    # the salt only needs to be unique per deployment purpose, not random.
    salt = hashlib.sha256(b"agentverse-vault-v1").digest()
    raw = hashlib.pbkdf2_hmac("sha256", master_key.encode(), salt, iterations=480_000)
    return base64.urlsafe_b64encode(raw)


class CredentialVault:
    """Encrypt and decrypt credential strings using Fernet (AES-256-GCM).

    The master key is NEVER stored anywhere — only the derived Fernet key is
    held in memory, and only as long as the vault instance lives.
    """

    def __init__(self, master_key: str, previous_master_keys: tuple[str, ...] = ()) -> None:
        # Encrypt with the current key; decrypt with it or any previous key
        # (VAULT_PREVIOUS_MASTER_KEYS) so every replica reads both old and new
        # ciphertext while ``agentverse vault-rotate`` re-encrypts the stores.
        primary_key = _derive_fernet_key(master_key)
        # Non-reversible identifier of the encryption key (rotation checkpoints).
        self._fingerprint = hashlib.sha256(b"agentverse-vault-fp:" + primary_key).hexdigest()[:16]
        keys = [Fernet(primary_key)]
        keys += [Fernet(_derive_fernet_key(k)) for k in previous_master_keys if k]
        self._fernet: Any = MultiFernet(keys) if len(keys) > 1 else keys[0]
        self._key: bytes | None = None  # populated only by from_byok()

    def fingerprint(self) -> str:
        """Identifier of the key this vault encrypts with (never the key itself)."""
        return getattr(self, "_fingerprint", "") or hashlib.sha256(
            b"agentverse-vault-fp:" + (self._key or b"")
        ).hexdigest()[:16]

    def encrypt(self, plaintext: str) -> str:
        """Encrypt *plaintext* and return a URL-safe ciphertext string."""
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt *ciphertext* back to plaintext.

        Raises ``cryptography.fernet.InvalidToken`` if the ciphertext was
        tampered with or encrypted with a different key.
        """
        return self._fernet.decrypt(ciphertext.encode()).decode()

    @classmethod
    def from_byok(cls, customer_key: bytes) -> CredentialVault:
        """Create a vault instance using a customer-provided encryption key (BYOK).

        The customer_key must be exactly 32 bytes. This allows enterprise customers
        to own their encryption keys rather than using AgentVerse's managed key.
        """
        if len(customer_key) != 32:
            raise ValueError("BYOK key must be exactly 32 bytes")
        vault = cls.__new__(cls)
        # Derive a Fernet key from the raw 32-byte customer key
        fernet_key = base64.urlsafe_b64encode(customer_key)
        vault._fernet = Fernet(fernet_key)
        vault._key = customer_key
        return vault

    def __repr__(self) -> str:
        return "CredentialVault(<key hidden>)"

    def __str__(self) -> str:
        return "CredentialVault(<key hidden>)"


def get_vault() -> CredentialVault:
    """Create a vault from the environment master key."""
    from app.core.secrets import SecretNotFoundError, read_secret

    is_production = os.environ.get("ENVIRONMENT", "development").lower() == "production"
    for secret_name in ("AGENTVERSE_VAULT_KEY", "VAULT_MASTER_KEY"):
        try:
            master_key = read_secret(secret_name)
            if is_production and master_key == _DEV_INSECURE_MASTER_KEY:
                raise RuntimeError(
                    "The dev-insecure-master-key vault key is not allowed in production."
                )
            return _cached_vault(master_key, _previous_master_keys())
        except SecretNotFoundError:
            pass

    if is_production:
        raise RuntimeError(
            "A vault master key is required in production; set "
            "AGENTVERSE_VAULT_KEY_FILE, AGENTVERSE_VAULT_KEY, "
            "VAULT_MASTER_KEY_FILE, or VAULT_MASTER_KEY."
        )

    master_key = _get_master_key()  # emits warning unless ALLOW_DEV_VAULT=true
    return _cached_vault(master_key, _previous_master_keys())


def _previous_master_keys() -> tuple[str, ...]:
    raw = os.environ.get("VAULT_PREVIOUS_MASTER_KEYS", "")
    return tuple(k.strip() for k in raw.split(",") if k.strip())


@functools.lru_cache(maxsize=8)
def _cached_vault(master_key: str, previous: tuple[str, ...]) -> CredentialVault:
    """One vault per key set: deriving a key runs PBKDF2 (480k iterations, ~0.3 s)."""
    return CredentialVault(master_key=master_key, previous_master_keys=previous)


# ── Offline master-key rotation (``agentverse vault-rotate``) ──────────────────


async def rotate_master_key(
    *,
    old: CredentialVault,
    new: CredentialVault,
    redis: Any = None,
    system_db: Any = None,
    tenant_db: Any = None,
    dry_run: bool = False,
    batch_size: int = 200,
    progress: Any = None,
    rotation_id: str | None = None,
) -> dict[str, Any]:
    """Re-encrypt every ciphertext store from ``old`` to ``new``.

    See :mod:`app.providers.vault_rotation`: all Postgres stores per tenant (RLS,
    batched, checkpointed, idempotent) plus the Redis stores; ``status`` is
    ``complete`` only when every value opens with the new key, after which
    ``VAULT_PREVIOUS_MASTER_KEYS`` can be retired.
    """
    from app.providers.vault_rotation import rotate_all_stores

    return await rotate_all_stores(
        old=old,
        new=new,
        rotation_id=rotation_id or new.fingerprint(),
        system_db=system_db,
        tenant_db=tenant_db,
        redis=redis,
        dry_run=dry_run,
        batch_size=batch_size,
        progress=progress,
    )
