"""Stateless, HMAC-signed webhook tokens for workflow triggers.

A token self-contains ``tenant_id`` and ``workflow_id`` plus an HMAC signature
over them, so the public webhook endpoint can authenticate and route a request
without storing a per-workflow secret. Deriving the token is deterministic, so
``publish`` can hand the caller a stable URL and the endpoint can re-verify it.

Signing key: ``WORKFLOW_WEBHOOK_SECRET`` (falls back to a dev key with a warning —
set it in any real deployment).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from app.observability.logging import get_logger

_log = get_logger(__name__)
_DEV_SECRET = "agentverse-dev-webhook-secret-change-me"  # dev fallback only


def _secret() -> bytes:
    s = os.getenv("WORKFLOW_WEBHOOK_SECRET", "")
    if not s:
        _log.warning("workflow_webhook_secret_unset_using_dev_default")
        s = _DEV_SECRET
    return s.encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_webhook_token(tenant_id: str, workflow_id: str) -> str:
    """Return a stable, signed token encoding (tenant_id, workflow_id)."""
    payload = f"{tenant_id}:{workflow_id}".encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()[:16]
    return f"{_b64(payload)}.{_b64(sig)}"


def verify_webhook_token(token: str) -> tuple[str, str] | None:
    """Return (tenant_id, workflow_id) if the token's signature is valid, else None."""
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _unb64(payload_b64)
        expected = hmac.new(_secret(), payload, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(_unb64(sig_b64), expected):
            return None
        tenant_id, workflow_id = payload.decode().split(":", 1)
        return tenant_id, workflow_id
    except Exception:
        return None
