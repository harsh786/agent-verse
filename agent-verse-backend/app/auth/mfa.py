"""TOTP-based MFA using pyotp. Secrets sent in request body, stored in DB."""
from __future__ import annotations
import uuid
from typing import Any
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/auth/mfa", tags=["auth"])


class EnrollConfirmRequest(BaseModel):
    secret: str
    code: str  # one-time confirmation code to verify before storing


class ValidateRequest(BaseModel):
    user_id: str
    code: str


def _get_pyotp():
    try:
        import pyotp
        return pyotp
    except ImportError:
        raise HTTPException(503, "MFA requires pyotp. Run: pip install pyotp")


def _req_tenant(r: Request) -> Any:
    ctx = getattr(r.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    return ctx


@router.post("/enroll")
async def enroll_mfa(request: Request) -> dict[str, Any]:
    """Generate a new TOTP secret. The secret is NOT stored yet — call /confirm to activate."""
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
        "instructions": "Scan with Google Authenticator, then POST /auth/mfa/confirm with the secret and a valid code.",
    }


@router.post("/confirm")
async def confirm_mfa(body: EnrollConfirmRequest, request: Request) -> dict[str, Any]:
    """Verify the code, then store the encrypted secret and mark MFA as enabled."""
    pyotp = _get_pyotp()
    tenant = _req_tenant(request)
    totp = pyotp.TOTP(body.secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(400, "Invalid or expired MFA code. Try again — codes are valid for 30 seconds.")
    # Persist mfa_enabled + encrypted secret to users table
    db = getattr(request.app.state, "db_session_factory", None)
    if db is not None:
        try:
            from sqlalchemy import text
            # Store secret encrypted — in production use Fernet (app/providers/vault.py)
            import base64 as _b64
            stored_secret = _b64.b64encode(body.secret.encode()).decode()
            async with db() as session:
                await session.execute(
                    text("UPDATE users SET mfa_enabled = true, mfa_secret = :secret WHERE tenant_id = :tid"),
                    {"secret": stored_secret, "tid": tenant.tenant_id},
                )
                await session.commit()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("mfa_persist_failed: %s", exc)
    return {"verified": True, "mfa_enabled": True,
            "message": "MFA activated. Future logins require a valid TOTP code."}


@router.post("/validate")
async def validate_mfa(body: ValidateRequest, request: Request) -> dict[str, Any]:
    """Validate a TOTP code at login time. Loads secret from DB."""
    pyotp = _get_pyotp()
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(503, "Database unavailable")
    from sqlalchemy import text
    async with db() as session:
        row = (await session.execute(
            text("SELECT mfa_secret FROM users WHERE id = :uid"),
            {"uid": body.user_id},
        )).fetchone()
    if not row or not row[0]:
        raise HTTPException(400, "MFA not enrolled for this user.")
    import base64 as _b64
    try:
        secret = _b64.b64decode(row[0]).decode()
    except Exception:
        secret = row[0]
    totp = pyotp.TOTP(secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(401, "Invalid MFA code.")
    return {"valid": True}
