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
* ``?api_key=`` (legacy / non-browser clients; opt-in per route).
"""

from __future__ import annotations

import base64
from binascii import Error as BinasciiError
from typing import Any

from starlette.websockets import WebSocket

__all__ = ["resolve_ws_tenant"]


def _key_from(websocket: WebSocket, *, allow_query_key: bool) -> str | None:
    key = websocket.headers.get("x-api-key")
    if key:
        return key
    auth = websocket.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    protocols = websocket.headers.get("sec-websocket-protocol", "").split(",")
    for protocol in (p.strip() for p in protocols):
        if protocol.startswith("av.v1."):
            encoded = protocol.removeprefix("av.v1.")
            try:
                return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
            except (BinasciiError, UnicodeDecodeError):
                return None
    if allow_query_key:
        return websocket.query_params.get("api_key") or None
    return None


async def resolve_ws_tenant(websocket: WebSocket, *, allow_query_key: bool = False) -> Any:
    """Return the authenticated ``TenantContext`` for a WebSocket, or ``None``."""
    key = _key_from(websocket, allow_query_key=allow_query_key)
    if not key:
        return None
    state = websocket.app.state
    resolver = getattr(state, "_tenant_key_resolver", None)
    try:
        if resolver is not None:
            return await resolver(key)
        svc = getattr(state, "tenant_service", None)
        return await svc.resolve_api_key(key) if svc is not None else None
    except Exception:
        return None
