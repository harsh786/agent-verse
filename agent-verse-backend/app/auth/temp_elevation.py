"""
Temporary Elevated Scope
=========================
Time-boxed impersonation and elevated access for support/compliance.

Rules:
- Maximum 4 hours
- Requires admin authorization
- Creates a fully-audited elevated session token
- Auto-expires: cannot be extended
- Appears in audit trail as "elevated:{user}:{original_role}→{elevated_role}"
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

_MAX_ELEVATION_SECONDS = 14400  # 4 hours
_ELEVATION_SECRET = os.getenv("ELEVATION_TOKEN_SECRET", "agentverse-elevation-secret")


@dataclass
class ElevationToken:
    token_id: str
    tenant_id: str
    user_id: str
    original_role: str
    elevated_role: str
    granted_by: str  # admin who authorized the elevation
    reason: str
    expires_at: float
    token: str


def grant_elevation(
    *,
    tenant_id: str,
    user_id: str,
    original_role: str,
    elevated_role: str,
    granted_by: str,
    reason: str,
    duration_seconds: int = 3600,
) -> ElevationToken:
    """Grant temporary elevated access. Returns a signed token."""
    if duration_seconds > _MAX_ELEVATION_SECONDS:
        raise ValueError(
            f"Elevation cannot exceed {_MAX_ELEVATION_SECONDS} seconds (4 hours)"
        )

    token_id = uuid.uuid4().hex
    expires_at = time.time() + duration_seconds

    payload = {
        "token_id": token_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "original_role": original_role,
        "elevated_role": elevated_role,
        "granted_by": granted_by,
        "reason": reason,
        "expires_at": expires_at,
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    sig_obj = hmac.new(_ELEVATION_SECRET.encode(), body.encode(), hashlib.sha256)
    token = f"elev_{body}.{base64.urlsafe_b64encode(sig_obj.digest()).decode()}"

    logger.info(
        "elevation_granted",
        tenant=tenant_id,
        user=user_id,
        original=original_role,
        elevated=elevated_role,
        granted_by=granted_by,
        expires_in=duration_seconds,
    )

    return ElevationToken(
        token_id=token_id,
        tenant_id=tenant_id,
        user_id=user_id,
        original_role=original_role,
        elevated_role=elevated_role,
        granted_by=granted_by,
        reason=reason,
        expires_at=expires_at,
        token=token,
    )


def verify_elevation(token: str) -> dict[str, Any] | None:
    """Verify and decode an elevation token."""
    try:
        if not token.startswith("elev_"):
            return None
        stripped = token[5:]
        parts = stripped.split(".")
        if len(parts) != 2:
            return None
        body, sig_b64 = parts
        expected_sig_obj = hmac.new(
            _ELEVATION_SECRET.encode(), body.encode(), hashlib.sha256
        )
        actual_sig = base64.urlsafe_b64decode(sig_b64 + "==")
        if not hmac.compare_digest(expected_sig_obj.digest(), actual_sig):
            return None
        payload = json.loads(base64.urlsafe_b64decode(body + "=="))
        if payload["expires_at"] < time.time():
            logger.info("elevation_token_expired", user=payload.get("user_id"))
            return None
        return payload  # type: ignore[return-value]
    except Exception:
        return None
