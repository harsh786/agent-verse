"""Test MFA end-to-end login gating."""
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_mfa_complete_endpoint_exists():
    """POST /auth/mfa/complete endpoint must exist."""
    from app.auth.mfa import router
    paths = [r.path for r in router.routes]
    assert any("complete" in p for p in paths), f"/auth/mfa/complete missing. Got: {paths}"


def test_mfa_complete_request_model():
    """MFACompleteRequest takes pending_token + code — not query params."""
    from app.auth.mfa import MFACompleteRequest
    r = MFACompleteRequest(pending_token="abc123", code="654321")
    assert r.pending_token == "abc123"
    assert r.code == "654321"


@pytest.mark.asyncio
async def test_mfa_complete_rejects_expired_token():
    """Expired or invalid pending_token must return 401."""
    from fastapi import HTTPException

    from app.auth.mfa import MFACompleteRequest, complete_mfa_login

    # Redis returns None for expired token
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.delete = AsyncMock()

    request = MagicMock()
    request.app.state._rate_limiter_redis = mock_redis

    with pytest.raises(HTTPException) as exc:
        await complete_mfa_login(
            MFACompleteRequest(pending_token="expired-token", code="123456"),
            request,
        )
    assert exc.value.status_code == 401
    assert "expired" in exc.value.detail.lower() or "invalid" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_mfa_complete_rejects_wrong_code():
    """Wrong TOTP code after valid pending token must return 401."""
    try:
        import base64

        import pyotp
    except ImportError:
        pytest.skip("pyotp not installed")

    from fastapi import HTTPException

    from app.auth.mfa import MFACompleteRequest, complete_mfa_login

    # Valid pending token
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=b"user-123")
    mock_redis.delete = AsyncMock()

    # DB has an MFA secret
    secret = pyotp.random_base32()
    encoded_secret = base64.b64encode(secret.encode()).decode()
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(fetchone=lambda: (encoded_secret,)))
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    def fake_db():
        return mock_session

    request = MagicMock()
    request.app.state._rate_limiter_redis = mock_redis
    request.app.state.db_session_factory = fake_db

    with pytest.raises(HTTPException) as exc:
        await complete_mfa_login(
            MFACompleteRequest(pending_token="valid-token", code="000000"),
            request,
        )
    assert exc.value.status_code == 401
