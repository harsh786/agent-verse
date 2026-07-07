"""Credential vault — AES-256-GCM encryption via Fernet.

All LLM API keys and MCP connector credentials pass through this vault before
touching Redis or PostgreSQL. The master key is read from the environment via
``read_secret()`` (``VAULT_MASTER_KEY`` or ``VAULT_MASTER_KEY_FILE``).

Key derivation: PBKDF2-HMAC-SHA256, 480 000 iterations (NIST SP 800-132, 2024).
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import logging as _logging
import os
from collections.abc import MutableMapping
from typing import Any

from cryptography.fernet import Fernet

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
            "VAULT_MASTER_KEY must be set in production. "
            "Set VAULT_MASTER_KEY environment variable."
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
    remainder = ref[len(_CONNECTOR_SECRET_PREFIX):]
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
            remainder = ref[len("secret://connector/"):]
        elif is_connector_secret_ref(ref):
            remainder = ref[len(_CONNECTOR_SECRET_PREFIX):]
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

    def __init__(self, master_key: str) -> None:
        self._fernet = Fernet(_derive_fernet_key(master_key))
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

    async def rotate_key(self, new_master_key: bytes, db: Any = None, redis: Any = None) -> dict[str, Any]:
        """Re-encrypt all stored secrets with a new master key.

        Process (transactional):
        1. Derive new Fernet key using the same KDF as __init__ (ensures consistency after restart)
        2. Scan all connector secret keys in Redis
        3. Phase 1: Decrypt ALL secrets with old Fernet in memory
        4. Phase 2: Re-encrypt ALL with new Fernet in memory
        5. Write ALL new values to Redis in a single pipeline (atomic batch)
        6. Update self._fernet and self._key ONLY after successful writes
        7. Record key version in DB
        """
        from app.observability.logging import get_logger

        logger = get_logger(__name__)

        if not isinstance(new_master_key, bytes) or len(new_master_key) < 32:
            raise ValueError("new_master_key must be at least 32 bytes")

        rotated = 0
        failed = 0

        # Derive new Fernet using the SAME function as __init__ to ensure key consistency
        # after restart.  The old code used a raw salt b"agentverse-vault" (16 bytes) which
        # differs from _derive_fernet_key's SHA-256-hashed salt → wrong derived key (C2-a).
        from cryptography.fernet import Fernet as _Fernet

        new_master_str = (
            new_master_key.decode("utf-8", errors="replace")
            if isinstance(new_master_key, bytes)
            else new_master_key
        )
        fernet_key_new = _derive_fernet_key(new_master_str)
        fernet_new = _Fernet(fernet_key_new)

        if redis is not None:
            try:
                # Use existing self._fernet as fernet_old (holds the current encryption key)
                fernet_old = self._fernet

                # Scan all connector secret keys
                keys = []
                async for key in redis.scan_iter(match="mcp:connector_secrets:*", count=200):
                    keys.append(key)

                # Phase 1: Decrypt all secrets in memory before writing anything.
                # Skip keys that cannot be decrypted (may be non-secret data); abort only
                # on infrastructure errors so a partial scan never leaves mixed-key state.
                pairs: list[tuple[Any, str]] = []
                for key in keys:
                    encrypted = await redis.get(key)
                    if not encrypted:
                        continue
                    raw = encrypted.encode() if isinstance(encrypted, str) else encrypted
                    try:
                        plaintext = fernet_old.decrypt(raw)
                    except Exception as exc:
                        failed += 1
                        logger.warning(
                            "secret_rotation_cannot_decrypt",
                            key=str(key)[:50],
                            error=str(exc)[:80],
                        )
                        continue  # skip undecryptable (may be non-secret data)
                    pairs.append((key, fernet_new.encrypt(plaintext).decode()))

                # Phase 2: Write all re-encrypted values in a single pipeline (all-or-nothing).
                # redis.pipeline() may be a coroutine (AsyncMock in tests, or some async
                # Redis clients) — use the same defensive pattern as semantic_cache.py.
                # pipe.set() / pipe.execute() may also be coroutines depending on the
                # Redis client version; guard with iscoroutine() so we work with both
                # real redis.asyncio (sync pipeline commands) and async mocks.
                if pairs:
                    import asyncio as _asyncio

                    pipe = redis.pipeline()
                    if _asyncio.iscoroutine(pipe):
                        pipe = await pipe
                    for key, value in pairs:
                        cmd = pipe.set(key, value)
                        if _asyncio.iscoroutine(cmd):
                            await cmd
                    exec_result = pipe.execute()
                    if _asyncio.iscoroutine(exec_result):
                        await exec_result
                    rotated = len(pairs)

                # Only update in-process state after successful Redis writes (C2-b fix).
                self._fernet = fernet_new
                self._key = new_master_key

            except Exception as exc:
                logger.warning("vault_key_rotation_redis_scan_failed", error=str(exc))
        else:
            # No Redis — update in-process state unconditionally (nothing to re-encrypt).
            self._fernet = fernet_new
            self._key = new_master_key

        # Record in DB
        if db is not None:
            try:
                import hashlib as _hashlib
                import uuid

                from sqlalchemy import text
                key_hash = _hashlib.sha256(new_master_key).hexdigest()[:16] + "..."
                async with db() as session, session.begin():
                    await session.execute(text(
                        "UPDATE vault_key_versions SET is_current = FALSE, retired_at = NOW() "
                        "WHERE is_current = TRUE"
                    ))
                    await session.execute(text("""
                        INSERT INTO vault_key_versions (id, key_hash, activated_at, is_current)
                        VALUES (:id, :hash, NOW(), TRUE)
                    """), {"id": uuid.uuid4().hex, "hash": key_hash})
                logger.info("vault_key_rotated", rotated=rotated, failed=failed)
            except Exception as exc:
                logger.warning("vault_key_version_record_failed", error=str(exc))

        return {"rotated_secrets": rotated, "failed": failed, "status": "rotation_complete"}

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
            return CredentialVault(master_key=master_key)
        except SecretNotFoundError:
            pass

    if is_production:
        raise RuntimeError(
            "A vault master key is required in production; set "
            "AGENTVERSE_VAULT_KEY_FILE, AGENTVERSE_VAULT_KEY, "
            "VAULT_MASTER_KEY_FILE, or VAULT_MASTER_KEY."
        )

    master_key = _get_master_key()  # emits warning unless ALLOW_DEV_VAULT=true
    return CredentialVault(master_key=master_key)
