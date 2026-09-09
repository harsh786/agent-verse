"""Short-lived SSE stream tokens.

EventSource cannot send request headers, so Server-Sent-Events endpoints have
historically taken the tenant's API key directly as a ``?api_key=`` query param —
which lands in access logs, browser history, and proxy caches, exposing a
permanent, full-scope credential.

Instead the client exchanges its API key (via a normal header-authenticated
request) for a short-lived, single-purpose token bound to the tenant, and passes
THAT in the stream URL. The blast radius of a leaked stream URL is then a
~10-minute, read-only event subscription for one tenant — not the tenant's
permanent API key, which could be replayed against every endpoint.

Signed with HMAC-SHA256 (same scheme as goal_tokens); stateless, so any replica
can verify without shared storage.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

# 10 minutes: long enough to open (and briefly reconnect) an SSE stream, short
# enough that a leaked stream URL expires quickly.
STREAM_TOKEN_TTL = 600

_SIGNING_SECRET = (
    os.getenv("STREAM_TOKEN_SECRET")
    or os.getenv("GOAL_TOKEN_SECRET")
    or "agentverse-stream-token-secret-change-in-prod"
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint_stream_token(
    *,
    tenant_id: str,
    key_id: str = "",
    ttl: int = STREAM_TOKEN_TTL,
) -> str:
    """Mint a short-lived signed token authorizing SSE reads for one tenant."""
    now = int(time.time())
    payload = {
        "typ": "stream",
        "tenant_id": tenant_id,
        "key_id": key_id,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex[:16],
    }
    header = _b64url(json.dumps({"alg": "HS256", "typ": "StreamToken"}).encode())
    body = _b64url(json.dumps(payload).encode())
    signing_input = f"{header}.{body}"
    sig = hmac.new(_SIGNING_SECRET.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url(sig)}"


def verify_stream_token(token: str) -> dict[str, Any] | None:
    """Verify and decode a stream token. Returns the payload or None if invalid."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header, body, sig = parts
        signing_input = f"{header}.{body}"
        expected_sig = hmac.new(
            _SIGNING_SECRET.encode(), signing_input.encode(), hashlib.sha256
        ).digest()
        token_sig = base64.urlsafe_b64decode(sig + "==")
        if not hmac.compare_digest(expected_sig, token_sig):
            return None
        payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(body + "=="))
        if payload.get("typ") != "stream":
            return None
        if payload.get("exp", 0) < int(time.time()):
            return None
        if not payload.get("tenant_id"):
            return None
        return payload
    except Exception as exc:
        logger.debug("stream_token_verify_error", error=str(exc)[:60])
        return None
