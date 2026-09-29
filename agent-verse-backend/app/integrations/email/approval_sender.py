"""Send HITL approval emails with signed approve/reject links.

P1.3: Generates HTML email with clickable Approve/Reject buttons and HMAC-signed URLs.

The signature binds ``request_id``, the owning ``tenant_id``, the action and an
expiry. It used to cover only ``request_id:action`` under the public default
secret ``changeme-please-set-HITL_EMAIL_SECRET`` in every environment, with no
expiry -- anyone could forge a never-expiring approve link for any request.
Production now refuses to sign or verify without ``HITL_EMAIL_SECRET`` or a key
derived from ``VAULT_MASTER_KEY`` (same scheme as ``app.auth.stream_tokens``).
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import os
import time
from urllib.parse import urlencode

from app.observability.logging import get_logger

logger = get_logger(__name__)
_SECRET_ENV = "HITL_EMAIL_SECRET"
_DEV_SECRET = "agentverse-dev-only-hitl-email-secret"

# Matches the "Links expire in 24 hours" promise in the email body.
EMAIL_LINK_TTL_S = 24 * 3600


def _signing_secret() -> str:
    """HMAC key for approval links; refuses the public default in production."""
    explicit = os.getenv(_SECRET_ENV)
    if explicit:
        return explicit
    master = os.getenv("VAULT_MASTER_KEY")
    if master:
        return hashlib.sha256(b"agentverse-hitl-email-link:" + master.encode()).hexdigest()
    if os.getenv("ENVIRONMENT", "development") == "production":
        raise RuntimeError("HITL_EMAIL_SECRET (or VAULT_MASTER_KEY) must be set in production")
    return _DEV_SECRET


def _sign(request_id: str, action: str, *, tenant_id: str, exp: int) -> str:
    """HMAC-SHA256 hex digest over (request_id, tenant_id, action, exp)."""
    payload = f"hitl-email:{request_id}:{tenant_id}:{action}:{int(exp)}".encode()
    return _hmac.new(_signing_secret().encode(), payload, hashlib.sha256).hexdigest()


def _verify(request_id: str, action: str, sig: str, *, tenant_id: str, exp: int) -> bool:
    """True only for an unexpired link signed for exactly this request, tenant and action."""
    if not sig or not tenant_id or int(exp) < int(time.time()):
        return False
    try:
        expected = _sign(request_id, action, tenant_id=tenant_id, exp=exp)
    except RuntimeError:
        logger.error("hitl_email_secret_missing")
        return False
    return _hmac.compare_digest(expected, sig)


def signed_link_query(
    request_id: str, action: str, *, tenant_id: str, ttl_s: int = EMAIL_LINK_TTL_S
) -> str:
    """``sig=...&exp=...`` query string for an approve/reject link."""
    exp = int(time.time()) + int(ttl_s)
    return urlencode({"sig": _sign(request_id, action, tenant_id=tenant_id, exp=exp), "exp": exp})


def build_decision_urls(base_url: str, request_id: str, *, tenant_id: str) -> tuple[str, str]:
    """(approve_url, reject_url) — the one link format every notifier uses."""
    base = base_url.rstrip("/")
    return (
        f"{base}/hitl/{request_id}/approve?"
        + signed_link_query(request_id, "approve", tenant_id=tenant_id),
        f"{base}/hitl/{request_id}/reject?"
        + signed_link_query(request_id, "reject", tenant_id=tenant_id),
    )


async def send_approval_email(
    *,
    to_email: str,
    goal_description: str,
    step_description: str,
    request_id: str,
    tenant_id: str,
    frontend_url: str,
    smtp_host: str = "localhost",
    smtp_port: int = 1025,
) -> bool:
    """Send an HTML email with clickable Approve/Reject buttons.

    Returns True on success, False on failure (import error or SMTP error).
    """
    try:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        import aiosmtplib

        approve_url, reject_url = build_decision_urls(
            frontend_url, request_id, tenant_id=tenant_id
        )

        html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px;">
<h2 style="color:#1e40af;">Action Required: Agent Approval</h2>
<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;
margin:16px 0;">
  <p><strong>Goal:</strong> {goal_description[:200]}</p>
  <p><strong>Action needing approval:</strong> {step_description[:300]}</p>
</div>
<p>The autonomous agent is waiting for your decision before proceeding.</p>
<div style="margin:24px 0;">
  <a href="{approve_url}" style="background:#16a34a;color:white;padding:12px 24px;
border-radius:6px;text-decoration:none;font-weight:bold;margin-right:12px;">Approve</a>
  <a href="{reject_url}" style="background:#dc2626;color:white;padding:12px 24px;
border-radius:6px;text-decoration:none;font-weight:bold;">Reject</a>
</div>
<p style="color:#64748b;font-size:12px;">Links expire in 24 hours. To approve/reject with
notes, visit the <a href="{frontend_url}/approvals">Approval Inbox</a>.</p>
</body></html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[AgentVerse] Approval Required: {step_description[:60]}"
        msg["From"] = "agentverse-noreply@agentverse.local"
        msg["To"] = to_email
        msg.attach(MIMEText(html, "html"))

        await aiosmtplib.send(msg, hostname=smtp_host, port=smtp_port, timeout=10)
        logger.info("approval_email_sent", to=to_email, request_id=request_id)
        return True
    except ImportError:
        logger.warning("aiosmtplib_not_installed", hint="pip install aiosmtplib")
        return False
    except Exception as exc:
        logger.warning("approval_email_failed", to=to_email, error=str(exc))
        return False
