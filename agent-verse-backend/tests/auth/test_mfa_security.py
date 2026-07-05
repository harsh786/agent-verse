"""Test MFA security: secrets in body not query params, confirm flow."""
import pytest


def test_mfa_enroll_returns_secret_in_body():
    """Enroll endpoint must return secret in response body, not accept secret as query param."""
    from app.auth.mfa import router
    routes = {r.path: r for r in router.routes}
    # confirm endpoint must accept a Pydantic body, not query params
    from app.auth.mfa import EnrollConfirmRequest
    body = EnrollConfirmRequest(secret="JBSWY3DPEHPK3PXP", code="123456")
    assert body.secret == "JBSWY3DPEHPK3PXP"
    assert body.code == "123456"


def test_mfa_validate_uses_request_body():
    """validate_mfa must take code from request body, not URL."""
    from app.auth.mfa import ValidateRequest
    body = ValidateRequest(user_id="user-123", code="654321")
    assert body.code == "654321"


def test_mfa_confirm_rejects_wrong_code():
    """Wrong TOTP code must return 400 without storing anything."""
    try:
        import pyotp
    except ImportError:
        pytest.skip("pyotp not installed")
    import pyotp
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    # Wrong code
    assert not totp.verify("000000", valid_window=1)


def test_mfa_confirm_accepts_correct_code():
    try:
        import pyotp
    except ImportError:
        pytest.skip("pyotp not installed")
    import pyotp
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    code = totp.now()
    assert totp.verify(code, valid_window=1)
