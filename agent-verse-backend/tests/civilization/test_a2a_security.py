from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.civilization.a2a_security import (
    A2AKey,
    A2AKeyPurpose,
    A2ASecurityError,
    A2ASecurityService,
    InMemoryA2AKeyProvider,
    InMemoryA2ANonceStore,
    sign_a2a,
)

NOW = datetime(2026, 8, 12, tzinfo=UTC)


def _key(
    key_id: str = "key-1",
    *,
    tenant: str = "tenant",
    purpose: A2AKeyPurpose = A2AKeyPurpose.REQUEST,
    revoked: bool = False,
) -> A2AKey:
    return A2AKey(
        tenant_id=tenant,
        key_id=key_id,
        purpose=purpose,
        secret=b"a" * 32,
        active_from=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=1),
        revoked=revoked,
    )


@pytest.mark.asyncio
async def test_valid_signature_resolves_tenant_and_replay_is_rejected() -> None:
    key = _key()
    service = A2ASecurityService(
        keys=InMemoryA2AKeyProvider((key,)), nonces=InMemoryA2ANonceStore()
    )
    body = b'{"goal":"safe"}'
    headers = sign_a2a(body, key=key, timestamp=NOW, nonce="nonce-1")
    assert await service.verify(body, headers, purpose=key.purpose, now=NOW) == "tenant"
    with pytest.raises(A2ASecurityError, match="nonce_replayed"):
        await service.verify(body, headers, purpose=key.purpose, now=NOW)


@pytest.mark.asyncio
async def test_forged_stale_wrong_tenant_and_revoked_keys_fail_closed() -> None:
    key = _key()
    body = b"payload"
    service = A2ASecurityService(
        keys=InMemoryA2AKeyProvider((key, _key("revoked", revoked=True))),
        nonces=InMemoryA2ANonceStore(),
    )
    forged = sign_a2a(body, key=key, timestamp=NOW, nonce="forged")
    forged = forged.__class__(
        forged.key_id, forged.timestamp, forged.nonce, "sha256=" + "0" * 64
    )
    with pytest.raises(A2ASecurityError, match="invalid_signature"):
        await service.verify(body, forged, purpose=key.purpose, now=NOW)
    stale = sign_a2a(body, key=key, timestamp=NOW - timedelta(hours=1), nonce="stale")
    with pytest.raises(A2ASecurityError, match="stale_timestamp"):
        await service.verify(body, stale, purpose=key.purpose, now=NOW)
    wrong = sign_a2a(body, key=key, timestamp=NOW, nonce="wrong")
    with pytest.raises(A2ASecurityError, match="tenant_mismatch"):
        await service.verify(
            body, wrong, purpose=key.purpose, expected_tenant_id="other", now=NOW
        )
    revoked_key = _key("revoked", revoked=True)
    revoked = sign_a2a(body, key=revoked_key, timestamp=NOW, nonce="revoked")
    with pytest.raises(A2ASecurityError, match="key_unavailable"):
        await service.verify(body, revoked, purpose=key.purpose, now=NOW)


@pytest.mark.asyncio
async def test_request_and_callback_keys_have_separate_purposes() -> None:
    request_key = _key("request")
    callback_key = _key("callback", purpose=A2AKeyPurpose.CALLBACK)
    service = A2ASecurityService(
        keys=InMemoryA2AKeyProvider((request_key, callback_key)),
        nonces=InMemoryA2ANonceStore(),
    )
    headers = sign_a2a(b"callback", key=callback_key, timestamp=NOW, nonce="n")
    assert (
        await service.verify(
            b"callback", headers, purpose=A2AKeyPurpose.CALLBACK, now=NOW
        )
        == "tenant"
    )
    with pytest.raises(A2ASecurityError, match="key_unavailable"):
        await service.verify(
            b"callback",
            headers,
            purpose=A2AKeyPurpose.REQUEST,
            now=NOW,
        )
