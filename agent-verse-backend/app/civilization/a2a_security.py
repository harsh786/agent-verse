"""Per-tenant, replay-safe A2A request and callback signatures."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol


class A2AKeyPurpose(StrEnum):
    REQUEST = "request"
    CALLBACK = "callback"


@dataclass(frozen=True, slots=True)
class A2AKey:
    tenant_id: str
    key_id: str
    purpose: A2AKeyPurpose
    secret: bytes
    active_from: datetime
    expires_at: datetime
    revoked: bool = False


@dataclass(frozen=True, slots=True)
class A2ASignedHeaders:
    key_id: str
    timestamp: str
    nonce: str
    signature: str
    algorithm: str = "hmac-sha256-v1"
    canonicalization: str = "json-c14n-v1"


class A2AKeyProvider(Protocol):
    async def get(self, key_id: str, purpose: A2AKeyPurpose) -> A2AKey | None: ...


class A2ANonceStore(Protocol):
    async def claim(
        self, tenant_id: str, key_id: str, nonce: str, *, retain_until: datetime
    ) -> bool: ...


class InMemoryA2AKeyProvider:
    def __init__(self, keys: tuple[A2AKey, ...]) -> None:
        self._keys = {(key.key_id, key.purpose): key for key in keys}

    async def get(self, key_id: str, purpose: A2AKeyPurpose) -> A2AKey | None:
        return self._keys.get((key_id, purpose))


class InMemoryA2ANonceStore:
    def __init__(self) -> None:
        import asyncio

        self._claims: dict[tuple[str, str, str], datetime] = {}
        self._lock = asyncio.Lock()

    async def claim(
        self, tenant_id: str, key_id: str, nonce: str, *, retain_until: datetime
    ) -> bool:
        async with self._lock:
            claim = (tenant_id, key_id, nonce)
            # Check replay BEFORE cleanup so frozen-time tests work correctly.
            if claim in self._claims:
                return False
            now = datetime.now(UTC)
            self._claims = {
                key: expiry for key, expiry in self._claims.items() if expiry > now
            }
            self._claims[claim] = retain_until
            return True


class A2ASecretResolver(Protocol):
    async def resolve(self, reference: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class A2AKeyMetadata:
    tenant_id: str
    key_id: str
    purpose: A2AKeyPurpose
    secret_reference: str
    active_from: datetime
    expires_at: datetime
    revoked: bool = False


class ExternalSecretA2AKeyProvider:
    """Resolves signing material from Vault/KMS only after metadata authorization."""

    def __init__(
        self, metadata: tuple[A2AKeyMetadata, ...], *, secrets: A2ASecretResolver
    ) -> None:
        self._metadata = {(item.key_id, item.purpose): item for item in metadata}
        self._secrets = secrets

    async def get(self, key_id: str, purpose: A2AKeyPurpose) -> A2AKey | None:
        metadata = self._metadata.get((key_id, purpose))
        if metadata is None:
            return None
        secret = await self._secrets.resolve(metadata.secret_reference)
        return A2AKey(
            tenant_id=metadata.tenant_id,
            key_id=metadata.key_id,
            purpose=metadata.purpose,
            secret=secret,
            active_from=metadata.active_from,
            expires_at=metadata.expires_at,
            revoked=metadata.revoked,
        )


class RedisA2ANonceStore:
    """Atomic replay claims shared by all workers and retained across process restart."""

    def __init__(self, redis: Any, *, prefix: str = "agentverse:a2a:nonce") -> None:
        self._redis = redis
        self._prefix = prefix

    async def claim(
        self, tenant_id: str, key_id: str, nonce: str, *, retain_until: datetime
    ) -> bool:
        digest = hashlib.sha256(f"{tenant_id}:{key_id}:{nonce}".encode()).hexdigest()
        lifetime = max(1, int((retain_until - datetime.now(UTC)).total_seconds()))
        accepted = await self._redis.set(
            f"{self._prefix}:{digest}", "claimed", ex=lifetime, nx=True
        )
        return bool(accepted)


class A2ASecurityError(PermissionError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_a2a_bytes(
    body: bytes,
    *,
    key_id: str,
    timestamp: str,
    nonce: str,
    purpose: A2AKeyPurpose,
) -> bytes:
    payload = {
        "algorithm": "hmac-sha256-v1",
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "canonicalization": "json-c14n-v1",
        "key_id": key_id,
        "nonce": nonce,
        "purpose": purpose.value,
        "timestamp": timestamp,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def sign_a2a(
    body: bytes,
    *,
    key: A2AKey,
    timestamp: datetime,
    nonce: str,
) -> A2ASignedHeaders:
    timestamp_text = timestamp.astimezone(UTC).isoformat()
    canonical = canonical_a2a_bytes(
        body,
        key_id=key.key_id,
        timestamp=timestamp_text,
        nonce=nonce,
        purpose=key.purpose,
    )
    signature = hmac.new(key.secret, canonical, hashlib.sha256).hexdigest()
    return A2ASignedHeaders(key.key_id, timestamp_text, nonce, f"sha256={signature}")


class A2ASecurityService:
    def __init__(
        self,
        *,
        keys: A2AKeyProvider,
        nonces: A2ANonceStore,
        maximum_clock_skew: timedelta = timedelta(minutes=5),
        replay_retention: timedelta = timedelta(hours=24),
        maximum_body_bytes: int = 1_048_576,
    ) -> None:
        self._keys = keys
        self._nonces = nonces
        self._skew = maximum_clock_skew
        self._retention = replay_retention
        self._maximum_body = maximum_body_bytes

    async def verify(
        self,
        body: bytes,
        headers: A2ASignedHeaders,
        *,
        purpose: A2AKeyPurpose,
        expected_tenant_id: str | None = None,
        now: datetime | None = None,
    ) -> str:
        current = now or datetime.now(UTC)
        if len(body) > self._maximum_body:
            raise A2ASecurityError("body_too_large")
        if headers.algorithm != "hmac-sha256-v1" or headers.canonicalization != "json-c14n-v1":
            raise A2ASecurityError("unsupported_signature_version")
        try:
            timestamp = datetime.fromisoformat(headers.timestamp)
        except ValueError as exc:
            raise A2ASecurityError("invalid_timestamp") from exc
        if timestamp.tzinfo is None or abs(current - timestamp.astimezone(UTC)) > self._skew:
            raise A2ASecurityError("stale_timestamp")
        key = await self._keys.get(headers.key_id, purpose)
        if key is None or key.revoked or not key.active_from <= current < key.expires_at:
            raise A2ASecurityError("key_unavailable")
        if expected_tenant_id is not None and key.tenant_id != expected_tenant_id:
            raise A2ASecurityError("tenant_mismatch")
        canonical = canonical_a2a_bytes(
            body,
            key_id=headers.key_id,
            timestamp=headers.timestamp,
            nonce=headers.nonce,
            purpose=purpose,
        )
        expected = "sha256=" + hmac.new(key.secret, canonical, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, headers.signature):
            raise A2ASecurityError("invalid_signature")
        claimed = await self._nonces.claim(
            key.tenant_id,
            key.key_id,
            headers.nonce,
            retain_until=current + self._retention,
        )
        if not claimed:
            raise A2ASecurityError("nonce_replayed")
        return key.tenant_id


__all__ = [
    "A2AKey",
    "A2AKeyMetadata",
    "A2AKeyPurpose",
    "A2ASecretResolver",
    "A2ASecurityError",
    "A2ASecurityService",
    "A2ASignedHeaders",
    "ExternalSecretA2AKeyProvider",
    "InMemoryA2AKeyProvider",
    "InMemoryA2ANonceStore",
    "RedisA2ANonceStore",
    "canonical_a2a_bytes",
    "sign_a2a",
]
