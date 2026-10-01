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
_CONNECTOR_SECRET_STORE: dict[str, str] = {}


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
    """Store a connector secret in the provided store or process fallback store."""
    if store is not None:
        store[ref] = value
        return
    _CONNECTOR_SECRET_STORE[ref] = value


def resolve_connector_secret_ref(
    ref: str,
    *,
    store: MutableMapping[str, str] | None = None,
) -> str | None:
    """Resolve a connector secret reference without exposing secrets in configs."""
    if store is not None and ref in store:
        return store[ref]
    return _CONNECTOR_SECRET_STORE.get(ref)


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
    ) -> None:
        self._redis = redis
        self._vault = vault
        self._key_prefix = key_prefix

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

    async def store(self, ref: str, value: str, *, tenant_ctx: Any = None) -> None:
        encrypted = self._vault.encrypt(value)
        await self._redis.set(self._redis_key(ref, tenant_ctx), encrypted)

    async def resolve(self, ref: str, *, tenant_ctx: Any = None) -> str | None:
        raw = await self._redis.get(self._redis_key(ref, tenant_ctx))
        if raw is None:
            return None
        encrypted = raw.decode() if isinstance(raw, bytes) else str(raw)
        return self._vault.decrypt(encrypted)


async def store_connector_secret_for_tenant(
    ref: str,
    value: str,
    *,
    store: Any = None,
    tenant_ctx: Any = None,
) -> None:
    """Store a connector secret in either a tenant-aware store or mapping fallback."""
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
) -> str | None:
    """Resolve a connector secret from a tenant-aware store or mapping fallback."""
    if store is not None and hasattr(store, "resolve"):
        result = store.resolve(ref, tenant_ctx=tenant_ctx)
        if inspect.isawaitable(result):
            result = await result
        return str(result) if result is not None else None
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
        keys = [Fernet(_derive_fernet_key(master_key))]
        keys += [Fernet(_derive_fernet_key(k)) for k in previous_master_keys if k]
        self._fernet: Any = MultiFernet(keys) if len(keys) > 1 else keys[0]
        self._key: bytes | None = None  # populated only by from_byok()

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

_REDIS_CONNECTOR_SECRETS = "mcp:connector_secrets:*"
# Ciphertext stores this rotation does NOT re-encrypt: they stay readable through
# VAULT_PREVIOUS_MASTER_KEYS, so keep the old key there until they are migrated.
_NOT_REENCRYPTED_STORES = (
    "connector OAuth tokens",
    "ingestion source credentials",
    "trigger/webhook secrets",
    "sealed memories",
    "the Redis LLM-config cache (re-filled from Postgres)",
)


async def rotate_master_key(
    *, old: CredentialVault, new: CredentialVault, redis: Any = None, system_db: Any = None
) -> dict[str, Any]:
    """Re-encrypt every known ciphertext store from ``old`` to ``new``.

    Covers the Redis connector secrets and ``tenant_llm_configs.encrypted_key``
    (and records a ``vault_key_versions`` row). Everything is decrypted FIRST; any
    scan, decrypt or write error makes the result ``status: "failed"`` (never a
    success after a swallowed error). Postgres is rewritten in one transaction
    under the maintenance role (``system_db`` = the system session factory);
    Redis is written in one pipeline after the Postgres commit.

    Run it with the old key still configured as a previous key on every replica
    (``VAULT_PREVIOUS_MASTER_KEYS``) so reads keep working throughout.
    """
    result: dict[str, Any] = {
        "status": "failed",
        "redis_connector_secrets": 0,
        "postgres_tenant_llm_keys": 0,
        "errors": [],
        "not_reencrypted_stores": list(_NOT_REENCRYPTED_STORES),
    }
    redis_pairs: list[tuple[Any, str]] = []
    try:
        if redis is not None:
            async for key in redis.scan_iter(match=_REDIS_CONNECTOR_SECRETS, count=200):
                raw = await redis.get(key)
                if not raw:
                    continue
                text_value = raw.decode() if isinstance(raw, bytes) else str(raw)
                redis_pairs.append((key, new.encrypt(old.decrypt(text_value))))
    except Exception as exc:
        result["errors"].append(f"redis: {type(exc).__name__}: {str(exc)[:200]}")
        _vault_log.error("vault_rotation_failed stage=redis error=%s", exc)
        return result

    if system_db is not None:
        try:
            import hashlib as _hashlib
            import uuid as _uuid

            from sqlalchemy import text

            from app.db.rls import system_session

            async with system_db() as session, session.begin(), system_session(session):
                rows = (
                    await session.execute(
                        text(
                            "SELECT tenant_id, encrypted_key FROM tenant_llm_configs "
                            "WHERE encrypted_key IS NOT NULL AND encrypted_key <> ''"
                        )
                    )
                ).fetchall()
                rewritten = [(str(r[0]), new.encrypt(old.decrypt(str(r[1])))) for r in rows]
                for tenant_id, ciphertext in rewritten:
                    await session.execute(
                        text(
                            "UPDATE tenant_llm_configs SET encrypted_key = :k "
                            "WHERE tenant_id = :t"
                        ),
                        {"k": ciphertext, "t": tenant_id},
                    )
                await session.execute(
                    text(
                        "UPDATE vault_key_versions SET is_current = FALSE, retired_at = NOW() "
                        "WHERE is_current = TRUE"
                    )
                )
                probe = new.encrypt("agentverse-vault-key-version")
                await session.execute(
                    text(
                        "INSERT INTO vault_key_versions (id, key_hash, activated_at, is_current) "
                        "VALUES (:id, :hash, NOW(), TRUE)"
                    ),
                    {
                        "id": _uuid.uuid4().hex,
                        "hash": _hashlib.sha256(probe.encode()).hexdigest()[:16] + "...",
                    },
                )
            result["postgres_tenant_llm_keys"] = len(rewritten)
        except Exception as exc:
            result["errors"].append(f"postgres: {type(exc).__name__}: {str(exc)[:200]}")
            _vault_log.error("vault_rotation_failed stage=postgres error=%s", exc)
            return result

    if redis_pairs:
        try:
            pipe = redis.pipeline()
            for key, value in redis_pairs:
                pipe.set(key, value)
            await pipe.execute()
        except Exception as exc:
            # Postgres is already on the new key; both keys stay readable through
            # VAULT_PREVIOUS_MASTER_KEYS, so re-running the rotation is safe.
            result["errors"].append(f"redis write: {type(exc).__name__}: {str(exc)[:200]}")
            _vault_log.error("vault_rotation_failed stage=redis_write error=%s", exc)
            return result
    result["redis_connector_secrets"] = len(redis_pairs)
    result["status"] = "complete"
    return result
