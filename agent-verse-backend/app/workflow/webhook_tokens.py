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


_DEV_ENVIRONMENTS = frozenset({"development", "dev", "test", "testing", "local"})


class WebhookSecretNotConfiguredError(RuntimeError):
    """WORKFLOW_WEBHOOK_SECRET is unset outside an explicit dev environment."""


def _secret() -> bytes:
    s = os.getenv("WORKFLOW_WEBHOOK_SECRET", "")
    if not s:
        # The dev key is public (it is in this file): with it anyone can forge a
        # valid token for any tenant's workflow. Never use it in production.
        # Only an EXPLICITLY local environment may use it. The old check read
        # ENVIRONMENT with a "development" default, so any deployment that did
        # not set ENVIRONMENT (or used "staging") accepted forgeable tokens.
        env = (os.getenv("ENVIRONMENT") or "").strip().lower()
        if env not in _DEV_ENVIRONMENTS:
            raise WebhookSecretNotConfiguredError(
                "WORKFLOW_WEBHOOK_SECRET must be set unless ENVIRONMENT is explicitly "
                "development/test/local"
            )
        _log.warning("workflow_webhook_secret_unset_using_dev_default")
        s = _DEV_SECRET
    return s.encode()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


_VERSION_SEP = "#v"


def make_webhook_token(tenant_id: str, workflow_id: str, version: int = 0) -> str:
    """Return a stable, signed token encoding (tenant_id, workflow_id, version).

    ``version`` is the workflow's webhook token version (bumped by rotation);
    version 0 keeps the original token format, so URLs issued before rotation
    existed stay valid until the first rotation.
    """
    text = f"{tenant_id}:{workflow_id}"
    if version:
        text += f"{_VERSION_SEP}{int(version)}"
    payload = text.encode()
    sig = hmac.new(_secret(), payload, hashlib.sha256).digest()[:16]
    return f"{_b64(payload)}.{_b64(sig)}"


def callback_signing_secret(tenant_id: str, workflow_id: str) -> str:
    """Per-(tenant, workflow) HMAC key for signing run-completion callbacks.

    Derived from ``WORKFLOW_WEBHOOK_SECRET`` with a distinct label so it can never
    be confused with (or used to forge) an inbound webhook token signature.
    """
    msg = f"callback:{tenant_id}:{workflow_id}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()


def verify_webhook_token_versioned(token: str) -> tuple[str, str, int] | None:
    """Return (tenant_id, workflow_id, version) for a validly signed token.

    The caller must still compare ``version`` with the workflow's current token
    version — a rotated-out token has a valid signature but an old version.
    """
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _unb64(payload_b64)
        expected = hmac.new(_secret(), payload, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(_unb64(sig_b64), expected):
            return None
        text = payload.decode()
        version = 0
        head, sep, tail = text.rpartition(_VERSION_SEP)
        if sep and tail.isdigit():
            text, version = head, int(tail)
        tenant_id, workflow_id = text.split(":", 1)
        return tenant_id, workflow_id, version
    except Exception:
        return None


def verify_webhook_token(token: str) -> tuple[str, str] | None:
    """Return (tenant_id, workflow_id) if the token's signature is valid, else None.

    Signature only — use :func:`verify_webhook_token_versioned` to also enforce
    rotation."""
    resolved = verify_webhook_token_versioned(token)
    return (resolved[0], resolved[1]) if resolved else None
