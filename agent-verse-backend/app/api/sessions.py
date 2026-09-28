"""User auth session management — NOT IMPLEMENTED (answers 501).

These endpoints used to read/delete ``session:{tenant_id}:{session_id}`` Redis
keys that no code ever writes, and neither API-key nor Keycloak-JWT auth ever
consults them. Listing therefore always returned nothing (or a fake "current"
row) and revoking answered 204 while the session kept working — a user trying
to log out a stolen session was told it succeeded. Until real session
recording on login/token issue AND per-request enforcement exist, every
endpoint here answers 501 so clients cannot mistake it for a working control.
"""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/auth/sessions", tags=["auth"])

SESSIONS_NOT_IMPLEMENTED_DETAIL = (
    "Login session tracking is not implemented: sessions are not recorded and "
    "cannot be listed or revoked. Revoke API keys via /tenants/me/keys, or end "
    "the SSO session at the identity provider."
)


def raise_sessions_not_implemented(request: Request) -> NoReturn:
    """401 when unauthenticated, otherwise an honest 501."""
    if getattr(request.state, "tenant", None) is None:
        raise HTTPException(401, "Unauthorized")
    raise HTTPException(status_code=501, detail=SESSIONS_NOT_IMPLEMENTED_DETAIL)


@router.get("")
async def list_active_sessions(request: Request) -> None:
    """List login sessions — not implemented (501)."""
    raise_sessions_not_implemented(request)


@router.delete("/{session_id}")
async def revoke_session(session_id: str, request: Request) -> None:
    """Revoke one login session — not implemented (501)."""
    raise_sessions_not_implemented(request)


@router.delete("")
async def revoke_all_other_sessions(request: Request) -> None:
    """Revoke all other login sessions — not implemented (501)."""
    raise_sessions_not_implemented(request)
