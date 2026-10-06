"""Self-service login-session management (``/auth/sessions``).

A login session is what a person holds after an interactive SSO sign-in (SAML,
Google): an ``avs_`` bearer token backed by a ``user_sessions`` row
(:mod:`app.auth.user_sessions`, Postgres-authoritative, FORCE RLS, resolved on
every request by ``TenantMiddleware``). These endpoints list and revoke the
CALLER's own sessions in the caller's tenant:

  GET    /auth/sessions               — live sessions (``current`` marks this one)
  DELETE /auth/sessions/{session_id}  — revoke one of them (404 if not the caller's)
  DELETE /auth/sessions               — revoke every other session ("sign out others")

``/tenants/me/sessions`` (the Settings page) serves the same data.

They used to answer 501 because nothing recorded sessions; SAML-01 added the
session store, so the 501 had become stale. A caller authenticated with an API
key (or an agent credential) holds no login session: that is a 409 naming the
key-management route — never an empty list that reads as "no other sessions".
A store outage is a 503, never a silent success.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter(prefix="/auth/sessions", tags=["auth"])

NOT_A_SESSION_DETAIL = (
    "This request is authenticated with an API key, not a sign-in session, so "
    "there are no login sessions to list or revoke. Rotate or revoke API keys via "
    "/tenants/me/keys."
)


def _caller(request: Request) -> tuple[str, str, str]:
    """(tenant_id, user_id, current token) of a session-authenticated caller."""
    from app.auth.user_sessions import is_session_token
    from app.tenancy.middleware import _extract_key

    ctx = getattr(request.state, "tenant", None)
    if ctx is None:
        raise HTTPException(401, "Unauthorized")
    token = _extract_key(request) or ""
    user_id = getattr(ctx, "user_id", None)
    if not user_id or not is_session_token(token):
        raise HTTPException(409, NOT_A_SESSION_DETAIL)
    return str(ctx.tenant_id), str(user_id), token


def _unavailable(exc: Any) -> HTTPException:
    return HTTPException(503, getattr(exc, "message", str(exc)), headers={"Retry-After": "5"})


async def list_caller_sessions(request: Request) -> list[dict[str, Any]]:
    from app.auth.user_sessions import SessionStoreUnavailableError, get_user_session_store

    tenant_id, user_id, token = _caller(request)
    try:
        return await get_user_session_store(request.app).list_user_sessions(
            tenant_id, user_id, current_token=token
        )
    except SessionStoreUnavailableError as exc:
        raise _unavailable(exc) from exc


async def revoke_caller_session(request: Request, session_id: str) -> Response:
    from app.auth.user_sessions import SessionStoreUnavailableError, get_user_session_store

    tenant_id, user_id, _ = _caller(request)
    try:
        revoked = await get_user_session_store(request.app).revoke_own_sessions(
            tenant_id, user_id, session_id=session_id
        )
    except SessionStoreUnavailableError as exc:
        raise _unavailable(exc) from exc
    if revoked == 0:
        # Unknown, already ended, or someone else's: indistinguishable on purpose.
        raise HTTPException(404, "Session not found")
    return Response(status_code=204)


@router.get("")
async def list_active_sessions(request: Request) -> list[dict[str, Any]]:
    """The caller's live login sessions, newest first."""
    return await list_caller_sessions(request)


@router.delete("/{session_id}", status_code=204)
async def revoke_session(session_id: str, request: Request) -> Response:
    """Revoke one of the caller's own sessions (its token stops working everywhere)."""
    return await revoke_caller_session(request, session_id)


@router.delete("")
async def revoke_all_other_sessions(request: Request) -> dict[str, int]:
    """Revoke every session of the caller except the one making this request."""
    from app.auth.user_sessions import SessionStoreUnavailableError, get_user_session_store

    tenant_id, user_id, token = _caller(request)
    try:
        revoked = await get_user_session_store(request.app).revoke_own_sessions(
            tenant_id, user_id, keep_token=token
        )
    except SessionStoreUnavailableError as exc:
        raise _unavailable(exc) from exc
    return {"revoked": revoked}
