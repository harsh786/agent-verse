from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.civilization.a2a_security import (
    A2AKeyMetadata,
    A2AKeyPurpose,
    ExternalSecretA2AKeyProvider,
    RedisA2ANonceStore,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        assert ex > 0 and nx
        if key in self.values:
            return False
        self.values[key] = value
        return True


class Resolver:
    def __init__(self) -> None:
        self.references: list[str] = []

    async def resolve(self, reference: str) -> bytes:
        self.references.append(reference)
        return b"kms-secret"


@pytest.mark.asyncio
async def test_redis_nonce_claim_survives_store_reconstruction() -> None:
    redis = FakeRedis()
    retain_until = datetime.now(UTC) + timedelta(hours=1)
    first = RedisA2ANonceStore(redis)
    restarted = RedisA2ANonceStore(redis)
    assert await first.claim("tenant", "key", "nonce", retain_until=retain_until)
    assert not await restarted.claim("tenant", "key", "nonce", retain_until=retain_until)


@pytest.mark.asyncio
async def test_external_key_provider_resolves_secret_by_reference() -> None:
    now = datetime.now(UTC)
    resolver = Resolver()
    provider = ExternalSecretA2AKeyProvider(
        (
            A2AKeyMetadata(
                tenant_id="tenant",
                key_id="key",
                purpose=A2AKeyPurpose.REQUEST,
                secret_reference="vault://tenant/a2a/key",
                active_from=now - timedelta(minutes=1),
                expires_at=now + timedelta(hours=1),
            ),
        ),
        secrets=resolver,
    )
    key = await provider.get("key", A2AKeyPurpose.REQUEST)
    assert key is not None and key.secret == b"kms-secret"
    assert resolver.references == ["vault://tenant/a2a/key"]
