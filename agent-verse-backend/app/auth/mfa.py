"""TOTP-based MFA (pyotp). Enrolment + verification endpoints."""
from __future__ import annotations
import uuid
from typing import Any
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/auth/mfa", tags=["auth"])


def _get_pyotp():
    try:
        import pyotp
        return pyotp
    except ImportError:
        raise HTTPException(503, "MFA requires pyotp. Install: pip install pyotp")


def _req_tenant(r: Request) -> Any:
    ctx = getattr(r.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


@router.post("/enroll")
async def enroll_mfa(request: Request) -> dict[str, Any]:
    """Generate TOTP secret + QR provisioning URI."""
    pyotp = _get_pyotp()
    tenant = _req_tenant(request)
    secret = pyotp.random_base32()
    email = getattr(tenant, "email", f"{tenant.tenant_id[:8]}@agentverse.ai")
    totp = pyotp.TOTP(secret)
    uri = totp.provisioning_uri(name=email, issuer_name="AgentVerse")
    return {
        "secret": secret,
        "provisioning_uri": uri,
        "qr_url": f"https://api.qrserver.com/v1/create-qr-code/?data={uri}&size=200x200",
        "instructions": "Scan the QR code with Google Authenticator or Authy, then call /auth/mfa/verify to confirm.",
    }


@router.post("/verify")
async def verify_mfa(request: Request, code: str, secret: str) -> dict[str, Any]:
    """Verify a TOTP code against the given secret."""
    pyotp = _get_pyotp()
    totp = pyotp.TOTP(secret)
    if not totp.verify(code, valid_window=1):
        raise HTTPException(400, "Invalid or expired MFA code. Codes are valid for 30 seconds.")
    # In production: persist mfa_enabled=True + encrypted secret to users table
    return {"verified": True, "message": "MFA enabled successfully. Store the secret securely."}


@router.post("/validate")
async def validate_mfa(request: Request, code: str, user_mfa_secret: str) -> dict[str, Any]:
    """Validate a TOTP code at login time."""
    pyotp = _get_pyotp()
    totp = pyotp.TOTP(user_mfa_secret)
    valid = totp.verify(code, valid_window=1)
    if not valid:
        raise HTTPException(401, "Invalid MFA code.")
    return {"valid": True}
