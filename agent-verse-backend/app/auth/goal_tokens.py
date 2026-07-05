"""
Short-Lived Goal Execution Tokens
===================================
When a goal starts executing (Celery worker picks it up), a short-lived
JWT token is minted for THAT goal execution only.

Token properties:
- Valid for 30 minutes (the execution window)
- Bound to: goal_id, tenant_id, agent_id, allowed_connectors
- If compromised: blast radius = 1 goal, not the entire tenant
- Automatically rotates: the worker uses this token, not the tenant key

This closes the "permanent credential exposure" risk in Celery workers.
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

# Default 30-minute TTL for goal execution
_GOAL_TOKEN_TTL = 1800

# Secret for HMAC signing (should come from env in production)
_SIGNING_SECRET = os.getenv(
    "GOAL_TOKEN_SECRET", "agentverse-goal-token-secret-change-in-prod"
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def mint_goal_token(
    *,
    goal_id: str,
    tenant_id: str,
    agent_id: str | None,
    allowed_connectors: list[str] | None = None,
    ttl: int = _GOAL_TOKEN_TTL,
) -> str:
    """Mint a short-lived signed token for a goal execution."""
    now = int(time.time())
    payload = {
        "sub": f"goal:{goal_id}",
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "allowed_connectors": allowed_connectors,
        "iat": now,
        "exp": now + ttl,
        "jti": uuid.uuid4().hex[:16],  # unique token ID (anti-replay)
    }
    header = _b64url(json.dumps({"alg": "HS256", "typ": "GoalToken"}).encode())
    body = _b64url(json.dumps(payload).encode())
    signing_input = f"{header}.{body}"
    sig = hmac.new(
        _SIGNING_SECRET.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64url(sig)}"


def verify_goal_token(token: str) -> dict[str, Any] | None:
    """Verify and decode a goal token. Returns payload or None if invalid."""
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
            logger.warning("goal_token_invalid_signature")
            return None
        payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(body + "=="))
        if payload.get("exp", 0) < int(time.time()):
            logger.warning("goal_token_expired", goal_id=payload.get("sub"))
            return None
        return payload
    except Exception as exc:
        logger.debug("goal_token_verify_error", error=str(exc)[:60])
        return None


class GoalTokenStore:
    """Tracks active goal tokens and supports revocation."""

    def __init__(self, redis: Any = None) -> None:
        self._redis = redis
        self._revoked: set[str] = set()

    def revoke(self, jti: str) -> None:
        self._revoked.add(jti)

    def is_revoked(self, jti: str) -> bool:
        return jti in self._revoked


# Module-level singleton
_goal_token_store = GoalTokenStore()
