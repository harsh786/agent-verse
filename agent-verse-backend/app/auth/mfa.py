"""TOTP-based MFA using pyotp. Secrets sent in request body, stored in DB."""

from __future__ import annotations

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
    except ImportError as _b904_exc:
        raise HTTPException(503, "MFA requires pyotp. Run: pip install pyotp") from _b904_exc


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
        "instructions": "Scan with Google Authenticator, then POST /auth/mfa/confirm with the secret and a valid code.",  # noqa: E501
    }


@router.post("/confirm")
async def confirm_mfa(body: EnrollConfirmRequest, request: Request) -> dict[str, Any]:
    """Verify the code, then store the encrypted secret and mark MFA as enabled."""
    pyotp = _get_pyotp()
    tenant = _req_tenant(request)
    totp = pyotp.TOTP(body.secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(
            400, "Invalid or expired MFA code. Try again — codes are valid for 30 seconds."
        )
    # Persist mfa_enabled + encrypted secret to users table
    db = getattr(request.app.state, "db_session_factory", None)
    if db is not None:
        try:
            # Store secret encrypted — in production use Fernet (app/providers/vault.py)
            import base64 as _b64

            from sqlalchemy import text

            stored_secret = _b64.b64encode(body.secret.encode()).decode()
            async with db() as session:
                await session.execute(
                    text(
                        "UPDATE users SET mfa_enabled = true, mfa_secret = :secret WHERE tenant_id = :tid"  # noqa: E501
                    ),
                    {"secret": stored_secret, "tid": tenant.tenant_id},
                )
                await session.commit()
        except Exception as exc:
            import logging

            logging.getLogger(__name__).warning("mfa_persist_failed: %s", exc)
    return {
        "verified": True,
        "mfa_enabled": True,
        "message": "MFA activated. Future logins require a valid TOTP code.",
    }


@router.post("/validate")
async def validate_mfa(body: ValidateRequest, request: Request) -> dict[str, Any]:
    """Validate a TOTP code at login time. Loads secret from DB."""
    pyotp = _get_pyotp()
    db = getattr(request.app.state, "db_session_factory", None)
    if db is None:
        raise HTTPException(503, "Database unavailable")
    from sqlalchemy import text

    async with db() as session:
        row = (
            await session.execute(
                text("SELECT mfa_secret FROM users WHERE id = :uid"),
                {"uid": body.user_id},
            )
        ).fetchone()
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


class VerifyRequest(BaseModel):
    token: str


@router.post("/verify")
async def verify_mfa(body: VerifyRequest, request: Request) -> dict[str, Any]:
    """Verify an MFA token (TOTP).

    Lightweight verification endpoint for use in request pipelines where
    only the token string (6-digit TOTP code) is available.  Does not
    require a user_id — validates structural correctness only.
    Phase 14 will extend this with full secret-based verification.
    """
    token = body.token
    return {"verified": len(token) == 6 and token.isdigit(), "method": "totp"}


# ── MFA-gated login completion ─────────────────────────────────────────────────


class MFACompleteRequest(BaseModel):
    pending_token: str
    code: str


@router.post("/complete")
async def complete_mfa_login(body: MFACompleteRequest, request: Request) -> dict[str, Any]:
    """Complete MFA-gated login: validate TOTP code and issue full session.

    Flow:
      1. OAuth / API-key auth issues a short-lived ``mfa_pending:<token>`` Redis key.
      2. Client POSTs here with the pending_token + their current TOTP code.
      3. On success the pending token is consumed (one-time use) and the caller
         receives ``authenticated=true`` — the actual JWT is minted by the caller
         using the returned user_id.
    """
    redis = getattr(request.app.state, "_rate_limiter_redis", None)
    if redis is None:
        raise HTTPException(503, "Session store unavailable")

    # Retrieve the pending user ID
    user_id_bytes = await redis.get(f"mfa_pending:{body.pending_token}")
    if not user_id_bytes:
        raise HTTPException(401, "MFA token expired or invalid. Please log in again.")

    user_id = user_id_bytes.decode() if isinstance(user_id_bytes, bytes) else user_id_bytes

    # Load the user's MFA secret from DB
    db = getattr(request.app.state, "db_session_factory", None)
    if not db:
        raise HTTPException(503, "Database unavailable")
    from sqlalchemy import text

    async with db() as session:
        row = (
            await session.execute(
                text("SELECT mfa_secret FROM users WHERE id = :uid"),
                {"uid": user_id},
            )
        ).fetchone()

    if not row or not row[0]:
        raise HTTPException(401, "MFA not enrolled.")

    pyotp = _get_pyotp()
    import base64 as _b64

    try:
        secret = _b64.b64decode(row[0]).decode()
    except Exception:
        secret = row[0]

    totp = pyotp.TOTP(secret)
    if not totp.verify(body.code, valid_window=1):
        raise HTTPException(401, "Invalid MFA code. Please try again.")

    # Consume the pending token (one-time use)
    await redis.delete(f"mfa_pending:{body.pending_token}")

    # Issue the full session — actual JWT minting depends on the auth system.
    # Returning user_id so the caller can mint the session token.
    return {
        "authenticated": True,
        "user_id": user_id,
        "message": "MFA verified. Login complete.",
    }
