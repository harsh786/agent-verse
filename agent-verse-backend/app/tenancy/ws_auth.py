"""Authentication for WebSocket routes.

``TenantMiddleware`` is a ``BaseHTTPMiddleware``, which Starlette only runs for
``http`` scopes — **WebSocket connections never pass through it**. Every
WebSocket handler therefore has to authenticate the connection itself, before
``accept()``. Handlers that did not (the org MCP socket took its tenant from an
attacker-controlled ``X-Tenant-Id`` header; the civilization socket accepted
anyone as tenant "unknown") were reachable with no credentials at all.

Credentials are accepted, in order, from:

* ``X-API-Key`` header,
* ``Authorization: Bearer <key>`` header,
* an ``av.v1.<base64url(key)>`` entry in ``Sec-WebSocket-Protocol`` (what the
  browser uses, since it cannot set headers on a WebSocket),
* ``?token=<stream token>`` — the short-lived, read-only token from
  ``GET /tenants/stream-token``, only on sockets that opt in
  (``allow_stream_token=True``) and never for a write socket.

``?api_key=`` is NOT accepted any more (it was opt-in on the org MCP and
civilization sockets): a key in a URL lands in access logs, proxy logs and
browser history, and the frontend stopped sending it.

After authentication the same per-tenant policies the HTTP pipeline applies
are enforced here — the tenant IP allowlist, an API key's explicit scopes and
its roles, and MFA (``X-MFA-Token`` header or an ``av.mfa.<token>`` subprotocol)
— all fail closed.
"""

from __future__ import annotations

import base64
from binascii import Error as BinasciiError
from typing import Any, cast

from starlette.requests import Request
from starlette.websockets import WebSocket

from app.observability.logging import get_logger

__all__ = ["resolve_ws_tenant"]

logger = get_logger(__name__)


def _subprotocol_value(websocket: WebSocket, prefix: str) -> str | None:
    protocols = websocket.headers.get("sec-websocket-protocol", "").split(",")
    for protocol in (p.strip() for p in protocols):
        if protocol.startswith(prefix):
            encoded = protocol.removeprefix(prefix)
            try:
                return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
            except (BinasciiError, UnicodeDecodeError):
                return None
    return None


def _key_from(websocket: WebSocket) -> str | None:
    key = websocket.headers.get("x-api-key")
    if key:
        return key
    auth = websocket.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return _subprotocol_value(websocket, "av.v1.")


async def _resolve_key(websocket: WebSocket, key: str) -> Any:
    state = websocket.app.state
    resolver = getattr(state, "_tenant_key_resolver", None)
    try:
        if resolver is not None:
            return await resolver(key)
        svc = getattr(state, "tenant_service", None)
        return await svc.resolve_api_key(key) if svc is not None else None
    except Exception as exc:
        # Unauthenticated (fail closed) — but never silently: a tenant-store
        # outage used to look exactly like a bad key.
        logger.warning("ws_key_resolution_failed", error=str(exc)[:200])
        return None


def _scope_denied(ctx: Any, required_scope: str | None, *, write: bool, path: str) -> bool:
    """Mirror the HTTP key-scope + role rules for a socket.

    ``required_scope=None`` is an endpoint with no registered scope: like the
    HTTP pipeline, a key minted with explicit scopes may not use it, and a
    socket that can write needs a role that may write unregistered endpoints.
    """
    from app.auth.scope_enforcement import _may_write_unregistered, scopes_for_roles

    key_scopes = tuple(getattr(ctx, "scopes", ()) or ())
    if key_scopes and (required_scope is None or required_scope not in key_scopes):
        return True
    roles = tuple(getattr(ctx, "roles", ()) or ())
    if required_scope is None:
        return write and bool(roles) and not _may_write_unregistered(roles, path)
    return bool(roles) and required_scope not in scopes_for_roles(roles)


async def _mfa_denied(websocket: WebSocket, tenant_id: str) -> bool:
    from app.core.config import get_settings

    if not get_settings().mfa_enforcement_enabled:
        return False
    from app.api.mfa import MFAStateUnavailableError, _mfa_db_store, check_mfa_session

    try:
        state = await _mfa_db_store.get(tenant_id)
        if not state.get("enabled"):
            return False
        token = websocket.headers.get("x-mfa-token") or _subprotocol_value(websocket, "av.mfa.")
        if not token:
            return True
        return await check_mfa_session(websocket.app, token, tenant_id) != "valid"
    except MFAStateUnavailableError:
        return True


async def resolve_ws_tenant(
    websocket: WebSocket,
    *,
    required_scope: str | None = None,
    write: bool = False,
    allow_stream_token: bool = False,
) -> Any:
    """Return the authenticated ``TenantContext`` for a WebSocket, or ``None``.

    ``None`` means "refuse the connection" — no/invalid credentials, or a
    tenant policy (IP allowlist, key scopes / roles, MFA) that does not allow
    this socket, or a policy store that cannot be read.
    """
    ctx: Any = None
    key = _key_from(websocket)
    if key:
        ctx = await _resolve_key(websocket, key)
    elif allow_stream_token and not write and websocket.query_params.get("token"):
        from app.auth.stream_tokens import verify_stream_token
        from app.tenancy.middleware import _stream_token_context

        claims = verify_stream_token(str(websocket.query_params.get("token")))
        if claims is not None:
            ctx = _stream_token_context(cast(Request, websocket), claims)
    if ctx is None:
        return None

    tenant_id = str(ctx.tenant_id)
    from app.auth.scope_enforcement import ScopeEnforcementMiddleware

    redis = getattr(websocket.app.state, "_rate_limiter_redis", None)
    denial = await ScopeEnforcementMiddleware._ip_allowlist_denial(
        cast(Request, websocket), tenant_id, redis
    )
    if denial is not None:
        logger.warning("ws_ip_allowlist_denied", tenant_id=tenant_id, status=denial.status_code)
        return None
    if key and _scope_denied(ctx, required_scope, write=write, path=websocket.url.path):
        logger.info("ws_scope_denied", tenant_id=tenant_id, required=required_scope)
        return None
    if await _mfa_denied(websocket, tenant_id):
        logger.info("ws_mfa_required", tenant_id=tenant_id)
        return None
    return ctx
