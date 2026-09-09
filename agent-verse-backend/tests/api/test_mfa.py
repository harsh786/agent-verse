"""Tests for MFA (TOTP-based 2FA) endpoints."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.mfa import (
    _generate_recovery_codes,
    _get_mfa_state,
    _hash_recovery_code,
    _mfa_store,
    _rate_limits,
    _used_totp_codes,
)
from app.api.mfa import router as mfa_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import SecurityHeadersMiddleware, TenantMiddleware

# ---------------------------------------------------------------------------
# Test fixtures / helpers
# ---------------------------------------------------------------------------

_CTX = TenantContext(tenant_id="tid-mfa-test", plan=PlanTier.PROFESSIONAL, api_key_id="kid-mfa")
_KEY = "ak_test_mfa_key_123"
_HEADERS = {"X-API-Key": _KEY}


def _make_app() -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(mfa_router)
    return app


@pytest.fixture(autouse=True)
def _clear_store() -> None:
    """Isolate each test by wiping the MFA store and rate-limit buckets for our test tenant."""
    _mfa_store.pop("tid-mfa-test", None)
    _rate_limits.pop("tid-mfa-test", None)
    _used_totp_codes.pop("tid-mfa-test", None)
    yield  # type: ignore[misc]
    _mfa_store.pop("tid-mfa-test", None)
    _rate_limits.pop("tid-mfa-test", None)
    _used_totp_codes.pop("tid-mfa-test", None)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def test_mfa_status_not_enabled() -> None:
    client = TestClient(_make_app())
    resp = client.get("/auth/mfa/status", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["has_pending_enrollment"] is False
    assert data["recovery_codes_count"] == 0


def test_mfa_status_unauthorized() -> None:
    client = TestClient(_make_app())
    resp = client.get("/auth/mfa/status")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Enrollment
# ---------------------------------------------------------------------------


def test_mfa_enroll_returns_secret_and_uri() -> None:
    client = TestClient(_make_app())
    resp = client.post("/auth/mfa/enroll", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "secret" in data
    assert "provisioning_uri" in data
    assert "otpauth://" in data["provisioning_uri"]
    assert "AgentVerse" in data["provisioning_uri"]
    assert len(data["secret"]) >= 16  # Base32 secret is at least 16 chars


def test_mfa_enroll_sets_pending_state() -> None:
    client = TestClient(_make_app())
    client.post("/auth/mfa/enroll", headers=_HEADERS)
    status_resp = client.get("/auth/mfa/status", headers=_HEADERS).json()
    assert status_resp["has_pending_enrollment"] is True
    assert status_resp["enabled"] is False


def test_mfa_enroll_when_already_enabled_returns_400() -> None:
    import pyotp

    state = _get_mfa_state("tid-mfa-test")
    state["enabled"] = True
    state["secret"] = pyotp.random_base32()
    state["recovery_codes_hashed"] = [_hash_recovery_code(c) for c in _generate_recovery_codes()]

    client = TestClient(_make_app())
    resp = client.post("/auth/mfa/enroll", headers=_HEADERS)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Full enrollment flow
# ---------------------------------------------------------------------------


def test_full_enrollment_flow() -> None:
    """enroll → verify-enrollment → status shows enabled + 10 recovery codes."""
    import pyotp

    client = TestClient(_make_app())

    enroll = client.post("/auth/mfa/enroll", headers=_HEADERS).json()
    secret = enroll["secret"]

    code = pyotp.TOTP(secret).now()
    complete = client.post("/auth/mfa/verify-enrollment", json={"code": code}, headers=_HEADERS)
    assert complete.status_code == 200
    data = complete.json()
    assert data["status"] == "enabled"
    assert len(data["recovery_codes"]) == 10
    for rc in data["recovery_codes"]:
        assert len(rc) == 11  # XXXXX-XXXXX
        assert rc[5] == "-"

    status_resp = client.get("/auth/mfa/status", headers=_HEADERS).json()
    assert status_resp["enabled"] is True
    assert status_resp["recovery_codes_count"] == 10


def test_verify_enrollment_without_enroll_first_returns_400() -> None:
    client = TestClient(_make_app())
    resp = client.post(
        "/auth/mfa/verify-enrollment", json={"code": "123456"}, headers=_HEADERS
    )
    assert resp.status_code == 400


def test_verify_enrollment_bad_code_returns_422() -> None:
    import pyotp

    state = _get_mfa_state("tid-mfa-test")
    state["pending_secret"] = pyotp.random_base32()

    client = TestClient(_make_app())
    resp = client.post(
        "/auth/mfa/verify-enrollment", json={"code": "000000"}, headers=_HEADERS
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Verify TOTP
# ---------------------------------------------------------------------------


def _enable_mfa(tenant_id: str = "tid-mfa-test") -> str:
    """Helper: enable MFA for a tenant and return the secret."""
    import pyotp

    secret = pyotp.random_base32()
    state = _get_mfa_state(tenant_id)
    state["enabled"] = True
    state["secret"] = secret
    state["recovery_codes_hashed"] = [_hash_recovery_code(c) for c in _generate_recovery_codes()]
    return secret


def test_verify_totp_valid_code() -> None:
    import pyotp

    secret = _enable_mfa()
    client = TestClient(_make_app())
    code = pyotp.TOTP(secret).now()
    resp = client.post("/auth/mfa/verify", json={"code": code}, headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["method"] == "totp"
    assert resp.json()["status"] == "verified"


def test_verify_totp_invalid_code_returns_422() -> None:
    _enable_mfa()
    client = TestClient(_make_app())
    resp = client.post("/auth/mfa/verify", json={"code": "000000"}, headers=_HEADERS)
    assert resp.status_code == 422


def test_verify_when_mfa_not_enabled_returns_400() -> None:
    client = TestClient(_make_app())
    resp = client.post("/auth/mfa/verify", json={"code": "123456"}, headers=_HEADERS)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Recovery codes
# ---------------------------------------------------------------------------


def test_recovery_code_works_and_is_consumed() -> None:
    """A recovery code is one-time use — second attempt should fail."""
    import pyotp

    codes = _generate_recovery_codes()
    state = _get_mfa_state("tid-mfa-test")
    state["enabled"] = True
    state["secret"] = pyotp.random_base32()
    state["recovery_codes_hashed"] = [_hash_recovery_code(c) for c in codes]

    client = TestClient(_make_app())

    resp = client.post("/auth/mfa/verify", json={"code": codes[0]}, headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["method"] == "recovery_code"
    assert resp.json()["remaining_recovery_codes"] == 9

    resp2 = client.post("/auth/mfa/verify", json={"code": codes[0]}, headers=_HEADERS)
    assert resp2.status_code == 422


def test_get_recovery_codes_count() -> None:
    _enable_mfa()
    client = TestClient(_make_app())
    resp = client.get("/auth/mfa/recovery-codes", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["remaining"] == 10


def test_get_recovery_codes_when_disabled_returns_400() -> None:
    client = TestClient(_make_app())
    resp = client.get("/auth/mfa/recovery-codes", headers=_HEADERS)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Disable
# ---------------------------------------------------------------------------


def test_disable_mfa_with_valid_code() -> None:
    import pyotp

    secret = _enable_mfa()
    client = TestClient(_make_app())
    code = pyotp.TOTP(secret).now()
    resp = client.post("/auth/mfa/disable", json={"code": code}, headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"

    status_resp = client.get("/auth/mfa/status", headers=_HEADERS).json()
    assert status_resp["enabled"] is False


def test_disable_mfa_with_invalid_code_returns_422() -> None:
    _enable_mfa()
    client = TestClient(_make_app())
    resp = client.post("/auth/mfa/disable", json={"code": "000000"}, headers=_HEADERS)
    assert resp.status_code == 422


def test_disable_when_not_enabled_returns_400() -> None:
    client = TestClient(_make_app())
    resp = client.post("/auth/mfa/disable", json={"code": "123456"}, headers=_HEADERS)
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Regenerate recovery codes
# ---------------------------------------------------------------------------


def test_regenerate_recovery_codes() -> None:
    import pyotp

    codes = _generate_recovery_codes()
    state = _get_mfa_state("tid-mfa-test")
    secret = pyotp.random_base32()
    state["enabled"] = True
    state["secret"] = secret
    state["recovery_codes_hashed"] = [_hash_recovery_code(c) for c in codes]

    client = TestClient(_make_app())
    code = pyotp.TOTP(secret).now()
    resp = client.post("/auth/mfa/regenerate", json={"code": code}, headers=_HEADERS)
    assert resp.status_code == 200
    new_codes = resp.json()["recovery_codes"]
    assert len(new_codes) == 10
    # New codes must differ from old ones (with overwhelming probability)
    assert set(new_codes) != set(codes)


def test_regenerate_with_invalid_code_returns_422() -> None:
    _enable_mfa()
    client = TestClient(_make_app())
    resp = client.post("/auth/mfa/regenerate", json={"code": "000000"}, headers=_HEADERS)
    assert resp.status_code == 422


def test_regenerate_when_not_enabled_returns_400() -> None:
    client = TestClient(_make_app())
    resp = client.post("/auth/mfa/regenerate", json={"code": "123456"}, headers=_HEADERS)
    assert resp.status_code == 400
