"""Functional coverage for app/auth/mfa.py's TOTP endpoints beyond the
routing/model smoke tests in test_mfa_module.py, test_mfa_login_flow.py and
test_mfa_security.py: the actual request-handler bodies for enroll, confirm,
validate, verify and complete -- including error branches (missing tenant,
missing pyotp, DB unavailable, wrong/expired code, malformed stored secret).
"""
from __future__ import annotations

import base64
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

pyotp = pytest.importorskip("pyotp")

from app.auth.mfa import (
    EnrollConfirmRequest,
    MFACompleteRequest,
    ValidateRequest,
    VerifyRequest,
    _get_pyotp,
    _req_tenant,
    complete_mfa_login,
    confirm_mfa,
    enroll_mfa,
    validate_mfa,
    verify_mfa,
)


def _make_session(fetchone_result):
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(fetchone=lambda: fetchone_result))
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


# ── _get_pyotp / _req_tenant ─────────────────────────────────────────────────


class TestGetPyotp:
    def test_returns_pyotp_module_when_installed(self):
        assert _get_pyotp() is pyotp

    def test_raises_503_when_pyotp_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pyotp", None)
        with pytest.raises(HTTPException) as exc:
            _get_pyotp()
        assert exc.value.status_code == 503
        assert "pyotp" in exc.value.detail


class TestReqTenant:
    def test_returns_tenant_when_present(self):
        request = MagicMock()
        request.state.tenant = "tenant-ctx"
        assert _req_tenant(request) == "tenant-ctx"

    def test_raises_401_when_tenant_missing(self):
        request = MagicMock()
        request.state = MagicMock(spec=[])  # no `tenant` attribute at all
        with pytest.raises(HTTPException) as exc:
            _req_tenant(request)
        assert exc.value.status_code == 401


# ── enroll_mfa ────────────────────────────────────────────────────────────────


class TestEnrollMfa:
    @pytest.mark.asyncio
    async def test_enroll_returns_secret_and_provisioning_uri(self):
        request = MagicMock()
        tenant = MagicMock()
        tenant.tenant_id = "tenant-abc12345"
        tenant.email = "user@example.com"
        request.state.tenant = tenant

        result = await enroll_mfa(request)

        assert "secret" in result
        assert result["provisioning_uri"].startswith("otpauth://")
        assert "user%40example.com" in result["provisioning_uri"] or "user@example.com" in result["provisioning_uri"]
        assert result["qr_url"].startswith("https://api.qrserver.com")

    @pytest.mark.asyncio
    async def test_enroll_falls_back_to_tenant_id_email_when_no_email_attr(self):
        request = MagicMock()
        tenant = MagicMock(spec=["tenant_id"])
        tenant.tenant_id = "tenant-abc12345"
        request.state.tenant = tenant

        result = await enroll_mfa(request)

        assert "tenant-a" in result["provisioning_uri"] or "tenant-ab" in result["provisioning_uri"]

    @pytest.mark.asyncio
    async def test_enroll_requires_tenant(self):
        request = MagicMock()
        request.state = MagicMock(spec=[])
        with pytest.raises(HTTPException) as exc:
            await enroll_mfa(request)
        assert exc.value.status_code == 401


# ── confirm_mfa ───────────────────────────────────────────────────────────────


class TestConfirmMfa:
    @pytest.mark.asyncio
    async def test_confirm_rejects_invalid_code(self):
        request = MagicMock()
        tenant = MagicMock()
        tenant.tenant_id = "tenant-1"
        request.state.tenant = tenant
        request.app.state = MagicMock(spec=[])

        secret = pyotp.random_base32()
        body = EnrollConfirmRequest(secret=secret, code="000000")

        with pytest.raises(HTTPException) as exc:
            await confirm_mfa(body, request)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_confirm_accepts_valid_code_and_persists_secret(self):
        request = MagicMock()
        tenant = MagicMock()
        tenant.tenant_id = "tenant-1"
        request.state.tenant = tenant

        secret = pyotp.random_base32()
        code = pyotp.TOTP(secret).now()
        body = EnrollConfirmRequest(secret=secret, code=code)

        session = _make_session(None)
        request.app.state.db_session_factory = lambda: session

        result = await confirm_mfa(body, request)

        assert result["verified"] is True
        assert result["mfa_enabled"] is True
        session.execute.assert_awaited_once()
        session.commit.assert_awaited_once()
        # secret should have been stored base64-encoded
        call_args = session.execute.call_args[0]
        stored_params = call_args[1]
        assert base64.b64decode(stored_params["secret"]).decode() == secret

    @pytest.mark.asyncio
    async def test_confirm_succeeds_even_without_db_configured(self):
        request = MagicMock()
        tenant = MagicMock()
        tenant.tenant_id = "tenant-1"
        request.state.tenant = tenant
        request.app.state = MagicMock(spec=[])  # no db_session_factory attr

        secret = pyotp.random_base32()
        code = pyotp.TOTP(secret).now()
        body = EnrollConfirmRequest(secret=secret, code=code)

        result = await confirm_mfa(body, request)
        assert result["verified"] is True

    @pytest.mark.asyncio
    async def test_confirm_swallows_db_persist_failure(self):
        request = MagicMock()
        tenant = MagicMock()
        tenant.tenant_id = "tenant-1"
        request.state.tenant = tenant

        secret = pyotp.random_base32()
        code = pyotp.TOTP(secret).now()
        body = EnrollConfirmRequest(secret=secret, code=code)

        def _boom():
            raise RuntimeError("db unreachable")

        request.app.state.db_session_factory = _boom

        # Persist failure must not surface to the caller -- confirm still
        # reports success since the code itself was valid.
        result = await confirm_mfa(body, request)
        assert result["verified"] is True


# ── validate_mfa ──────────────────────────────────────────────────────────────


class TestValidateMfa:
    @pytest.mark.asyncio
    async def test_validate_503_when_db_unavailable(self):
        request = MagicMock()
        request.app.state = MagicMock(spec=[])
        with pytest.raises(HTTPException) as exc:
            await validate_mfa(ValidateRequest(user_id="u1", code="123456"), request)
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_validate_400_when_user_not_enrolled(self):
        request = MagicMock()
        session = _make_session(None)
        request.app.state.db_session_factory = lambda: session
        with pytest.raises(HTTPException) as exc:
            await validate_mfa(ValidateRequest(user_id="u1", code="123456"), request)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_validate_401_on_wrong_code(self):
        request = MagicMock()
        secret = pyotp.random_base32()
        stored = base64.b64encode(secret.encode()).decode()
        session = _make_session((stored,))
        request.app.state.db_session_factory = lambda: session

        with pytest.raises(HTTPException) as exc:
            await validate_mfa(ValidateRequest(user_id="u1", code="000000"), request)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_validate_succeeds_with_correct_code(self):
        request = MagicMock()
        secret = pyotp.random_base32()
        stored = base64.b64encode(secret.encode()).decode()
        session = _make_session((stored,))
        request.app.state.db_session_factory = lambda: session

        code = pyotp.TOTP(secret).now()
        result = await validate_mfa(ValidateRequest(user_id="u1", code=code), request)
        assert result == {"valid": True}

    @pytest.mark.asyncio
    async def test_validate_handles_non_base64_stored_secret(self):
        # Secrets stored pre-encoding-fix (or corrupted) fall back to using
        # the raw column value as the TOTP secret directly.
        request = MagicMock()
        raw_secret = pyotp.random_base32()
        session = _make_session((raw_secret,))
        request.app.state.db_session_factory = lambda: session

        code = pyotp.TOTP(raw_secret).now()
        result = await validate_mfa(ValidateRequest(user_id="u1", code=code), request)
        assert result == {"valid": True}


# ── verify_mfa ────────────────────────────────────────────────────────────────


class TestVerifyMfa:
    @pytest.mark.asyncio
    async def test_six_digit_token_is_structurally_verified(self):
        result = await verify_mfa(VerifyRequest(token="123456"), MagicMock())
        assert result == {"verified": True, "method": "totp"}

    @pytest.mark.asyncio
    async def test_non_digit_token_fails_structural_check(self):
        result = await verify_mfa(VerifyRequest(token="12a456"), MagicMock())
        assert result["verified"] is False

    @pytest.mark.asyncio
    async def test_wrong_length_token_fails_structural_check(self):
        result = await verify_mfa(VerifyRequest(token="12345"), MagicMock())
        assert result["verified"] is False


# ── complete_mfa_login ─────────────────────────────────────────────────────────


class TestCompleteMfaLogin:
    @pytest.mark.asyncio
    async def test_503_when_redis_unavailable(self):
        request = MagicMock()
        request.app.state = MagicMock(spec=[])
        with pytest.raises(HTTPException) as exc:
            await complete_mfa_login(
                MFACompleteRequest(pending_token="t", code="123456"), request
            )
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_401_when_pending_token_expired(self):
        request = MagicMock()
        redis = MagicMock()
        redis.get = AsyncMock(return_value=None)
        request.app.state._rate_limiter_redis = redis

        with pytest.raises(HTTPException) as exc:
            await complete_mfa_login(
                MFACompleteRequest(pending_token="expired", code="123456"), request
            )
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_503_when_db_unavailable_after_valid_pending_token(self):
        request = MagicMock()
        redis = MagicMock()
        redis.get = AsyncMock(return_value=b"user-1")
        request.app.state._rate_limiter_redis = redis
        request.app.state.db_session_factory = None

        with pytest.raises(HTTPException) as exc:
            await complete_mfa_login(
                MFACompleteRequest(pending_token="tok", code="123456"), request
            )
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_401_when_user_not_enrolled(self):
        request = MagicMock()
        redis = MagicMock()
        redis.get = AsyncMock(return_value=b"user-1")
        request.app.state._rate_limiter_redis = redis
        session = _make_session(None)
        request.app.state.db_session_factory = lambda: session

        with pytest.raises(HTTPException) as exc:
            await complete_mfa_login(
                MFACompleteRequest(pending_token="tok", code="123456"), request
            )
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_success_consumes_pending_token_and_returns_user_id(self):
        request = MagicMock()
        redis = MagicMock()
        redis.get = AsyncMock(return_value=b"user-1")
        redis.delete = AsyncMock()
        request.app.state._rate_limiter_redis = redis

        secret = pyotp.random_base32()
        stored = base64.b64encode(secret.encode()).decode()
        session = _make_session((stored,))
        request.app.state.db_session_factory = lambda: session

        code = pyotp.TOTP(secret).now()
        result = await complete_mfa_login(
            MFACompleteRequest(pending_token="valid-token", code=code), request
        )

        assert result["authenticated"] is True
        assert result["user_id"] == "user-1"
        redis.delete.assert_awaited_once_with("mfa_pending:valid-token")

    @pytest.mark.asyncio
    async def test_success_decodes_string_user_id_bytes(self):
        # user_id_bytes may already be a plain str depending on the Redis
        # client's decode_responses setting -- both forms must work.
        request = MagicMock()
        redis = MagicMock()
        redis.get = AsyncMock(return_value="user-2")  # not bytes
        redis.delete = AsyncMock()
        request.app.state._rate_limiter_redis = redis

        secret = pyotp.random_base32()
        stored = base64.b64encode(secret.encode()).decode()
        session = _make_session((stored,))
        request.app.state.db_session_factory = lambda: session

        code = pyotp.TOTP(secret).now()
        result = await complete_mfa_login(
            MFACompleteRequest(pending_token="tok", code=code), request
        )
        assert result["user_id"] == "user-2"

    @pytest.mark.asyncio
    async def test_success_handles_non_base64_stored_secret(self):
        # Mirrors validate_mfa's fallback: if the stored secret isn't valid
        # base64, use the raw column value as the TOTP secret directly.
        request = MagicMock()
        redis = MagicMock()
        redis.get = AsyncMock(return_value=b"user-3")
        redis.delete = AsyncMock()
        request.app.state._rate_limiter_redis = redis

        raw_secret = pyotp.random_base32()
        session = _make_session((raw_secret,))
        request.app.state.db_session_factory = lambda: session

        code = pyotp.TOTP(raw_secret).now()
        result = await complete_mfa_login(
            MFACompleteRequest(pending_token="tok", code=code), request
        )
        assert result["authenticated"] is True
        assert result["user_id"] == "user-3"
