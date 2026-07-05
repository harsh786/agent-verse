"""Test MFA module structure and TOTP logic."""
import pytest


def test_mfa_router_has_correct_routes():
    from app.auth.mfa import router
    paths = [r.path for r in router.routes]
    assert any("enroll" in p for p in paths), f"enroll route missing. Got: {paths}"
    assert any("verify" in p for p in paths), f"verify route missing. Got: {paths}"
    assert any("validate" in p for p in paths), f"validate route missing. Got: {paths}"


def test_mfa_verify_correct_totp_code():
    """Correct TOTP code must pass verification."""
    try:
        import pyotp
    except ImportError:
        pytest.skip("pyotp not installed")
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    code = totp.now()
    assert totp.verify(code, valid_window=1) is True


def test_mfa_verify_wrong_code():
    """Wrong TOTP code must fail."""
    try:
        import pyotp
    except ImportError:
        pytest.skip("pyotp not installed")
    secret = pyotp.random_base32()
    totp = pyotp.TOTP(secret)
    assert totp.verify("000000", valid_window=1) is False


def test_policy_rules_router_has_correct_routes():
    from app.api.policy_rules import router
    paths = [r.path for r in router.routes]
    assert any("policy-rules" in p for p in paths), f"policy-rules routes missing. Got: {paths}"


def test_public_status_router_has_status_endpoint():
    from app.api.public_status import router
    paths = [r.path for r in router.routes]
    assert any("status" in p for p in paths), f"/status route missing. Got: {paths}"
