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
# The published dev key is allowed ONLY here (SECRET-05): any other environment
# name (staging, prod-eu, qa, a typo of "production") needs a real key.
_DEV_KEY_ENVIRONMENTS = frozenset({"development", "dev", "local", "test", "testing"})


def _dev_key_allowed() -> bool:
    return os.getenv("ENVIRONMENT", "development").strip().lower() in _DEV_KEY_ENVIRONMENTS


# The names the master key is read from, in precedence order (each also as *_FILE).
MASTER_KEY_NAMES: tuple[str, ...] = ("AGENTVERSE_VAULT_KEY", "VAULT_MASTER_KEY")

# Which kind of process this is ("api", "worker", "beat", "cli"): named in
# decrypt-failure messages so an operator knows WHICH deployment lacks the key.
_PROCESS_ROLE = "api"


def set_process_role(role: str) -> None:
    global _PROCESS_ROLE
    _PROCESS_ROLE = role or "api"


def process_role() -> str:
    return _PROCESS_ROLE


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
    if not _dev_key_allowed():
        raise RuntimeError(
            "VAULT_MASTER_KEY must be set in production (and in every environment other "
            "than development/test)."
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
        # Non-reversible identifier of the encryption key (rotation checkpoints,
        # stored with BYOK ciphertext so a decrypt failure can name the key that
        # is missing — BYOK-2). Derived from the PBKDF2 output, never the key.
        self._fingerprint = _key_fingerprint(primary_key)
        previous = [_derive_fernet_key(k) for k in previous_master_keys if k]
        self._previous_fingerprints = tuple(_key_fingerprint(k) for k in previous)
        keys = [Fernet(primary_key)]
        keys += [Fernet(k) for k in previous]
        self._fernet: Any = MultiFernet(keys) if len(keys) > 1 else keys[0]
        self._key: bytes | None = None  # populated only by from_byok()

    def fingerprint(self) -> str:
        """Identifier of the key this vault encrypts with (never the key itself)."""
        return getattr(self, "_fingerprint", "") or hashlib.sha256(
            b"agentverse-vault-fp:" + (self._key or b"")
        ).hexdigest()[:16]

    def fingerprints(self) -> tuple[str, ...]:
        """The current key's fingerprint, then those of the decrypt-only previous keys."""
        return (self.fingerprint(), *getattr(self, "_previous_fingerprints", ()))

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


def _key_fingerprint(derived_key: bytes) -> str:
    return hashlib.sha256(b"agentverse-vault-fp:" + derived_key).hexdigest()[:16]


def _configured_master_key() -> str | None:
    """The master key from AGENTVERSE_VAULT_KEY[_FILE] / VAULT_MASTER_KEY[_FILE].

    An empty / blank value is NOT a key: production compose used to pass
    ``AGENTVERSE_VAULT_KEY=${AGENTVERSE_VAULT_KEY:-}`` and the vault then
    encrypted everything with the empty string. Two different values under the
    two names are refused — one of them silently won, so a process configured
    with only the other name used a different key (BYOK-2).
    """
    from app.core.secrets import SecretNotFoundError, read_secret

    found: dict[str, str] = {}
    for secret_name in MASTER_KEY_NAMES:
        try:
            value = read_secret(secret_name).strip()
        except SecretNotFoundError:
            continue
        if value:
            found[secret_name] = value
    if len(set(found.values())) > 1:
        fps = {n: _key_fingerprint(_derive_fernet_key(v)) for n, v in found.items()}
        raise RuntimeError(
            "Conflicting vault master keys: "
            + " and ".join(f"{n} (fingerprint {fp})" for n, fp in fps.items())
            + " hold different values. Set only VAULT_MASTER_KEY, with the same value on "
            "the API and every worker / beat process."
        )
    return next(iter(found.values()), None)


def get_vault() -> CredentialVault:
    """Create a vault from the environment master key."""
    dev_allowed = _dev_key_allowed()
    master_key = _configured_master_key()
    if master_key is not None:
        if not dev_allowed and master_key == _DEV_INSECURE_MASTER_KEY:
            raise RuntimeError(
                "The dev-insecure-master-key vault key is only allowed in "
                "development/test, not in ENVIRONMENT="
                f"{os.environ.get('ENVIRONMENT', '')!r}."
            )
        return _cached_vault(master_key, _previous_master_keys())

    if not dev_allowed:
        raise RuntimeError(
            "A vault master key is required in production (and in every environment "
            "other than development/test); set "
            "AGENTVERSE_VAULT_KEY_FILE, AGENTVERSE_VAULT_KEY, "
            "VAULT_MASTER_KEY_FILE, or VAULT_MASTER_KEY."
        )

    master_key = _get_master_key()  # emits warning unless ALLOW_DEV_VAULT=true
    return _cached_vault(master_key, _previous_master_keys())


def assert_vault_key_configured(role: str) -> CredentialVault:
    """Startup check for a process that encrypts / decrypts vault data.

    Raises ``RuntimeError`` (no key outside development/test, the dev key outside
    development/test, conflicting names). The API always called ``get_vault()``
    while building the app; Celery workers and beat never did, so a worker pod
    deployed without VAULT_MASTER_KEY started and then failed every BYOK goal.
    """
    set_process_role(role)
    return get_vault()


class VaultKeyMismatchError(RuntimeError):
    """A vault value does not open with this process's key (message names fingerprints)."""


def explain_decrypt_failure(stored_fingerprint: str | None) -> str:
    """Why a vault value does not open here, by key FINGERPRINT (never the key).

    ``stored_fingerprint`` is the fingerprint recorded when the value was
    encrypted. Compared with this process's current + previous keys (and, when
    known, with the fleet's canary) it tells which side is misconfigured.
    """
    role = process_role()
    try:
        local = get_vault()
    except Exception as exc:
        return (
            f"this {role} process has no usable vault master key ({exc}). Set "
            "VAULT_MASTER_KEY to the same value as on the API"
        )
    fps = local.fingerprints()
    here = f"this {role} process has vault key fingerprint {fps[0]}"
    if len(fps) > 1:
        here += f" (previous: {', '.join(fps[1:])})"
    if not stored_fingerprint:
        return (
            f"no key fingerprint was stored with it (saved before fingerprints were "
            f"recorded); {here}. If the API uses another VAULT_MASTER_KEY, set the same "
            "value on every API, worker and beat process; otherwise re-save the key"
        )
    if stored_fingerprint in fps:
        return (
            f"it was encrypted with vault key fingerprint {stored_fingerprint}, which "
            f"this {role} process holds, but it does not open: the stored value is "
            "corrupt. Re-save it"
        )
    from app.providers.vault_canary import last_canary_result

    blame = ""
    canary = last_canary_result()
    if canary is not None and canary.canary_fingerprint:
        if canary.canary_fingerprint == stored_fingerprint:
            blame = (
                f" The API's vault canary is also under {stored_fingerprint}: this {role} "
                "process is the misconfigured side."
            )
        elif canary.canary_fingerprint in fps:
            blame = (
                f" The API's vault canary opens here: the process that saved this value "
                f"used another key ({stored_fingerprint}); re-save it."
            )
    return (
        f"vault key mismatch: it was encrypted with vault key fingerprint "
        f"{stored_fingerprint} but {here}. Set the same VAULT_MASTER_KEY on the API "
        f"and on every worker and beat process.{blame}"
    )


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
