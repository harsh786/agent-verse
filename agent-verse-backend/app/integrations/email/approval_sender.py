"""Send HITL approval emails with signed approve/reject links.

P1.3: Generates HTML email with clickable Approve/Reject buttons and HMAC-signed URLs.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import os
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)
_SECRET_ENV = "HITL_EMAIL_SECRET"


def _sign(request_id: str, action: str) -> str:
    """Return a 32-char HMAC-SHA256 hex digest for the (request_id, action) pair."""
    secret = os.getenv(_SECRET_ENV, "changeme-please-set-HITL_EMAIL_SECRET")
    payload = f"{request_id}:{action}".encode()
    return _hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()[:32]


def _verify(request_id: str, action: str, sig: str) -> bool:
    """Return True if sig is the correct signature for (request_id, action)."""
    expected = _sign(request_id, action)
    return _hmac.compare_digest(expected, sig)


async def send_approval_email(
    *,
    to_email: str,
    goal_description: str,
    step_description: str,
    request_id: str,
    frontend_url: str,
    smtp_host: str = "localhost",
    smtp_port: int = 1025,
) -> bool:
    """Send an HTML email with clickable Approve/Reject buttons.

    Returns True on success, False on failure (import error or SMTP error).
    """
    try:
        import aiosmtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        approve_sig = _sign(request_id, "approve")
        reject_sig = _sign(request_id, "reject")
        approve_url = f"{frontend_url}/hitl/{request_id}/approve?sig={approve_sig}"
        reject_url = f"{frontend_url}/hitl/{request_id}/reject?sig={reject_sig}"

        html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px;">
<h2 style="color:#1e40af;">Action Required: Agent Approval</h2>
<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin:16px 0;">
  <p><strong>Goal:</strong> {goal_description[:200]}</p>
  <p><strong>Action needing approval:</strong> {step_description[:300]}</p>
</div>
<p>The autonomous agent is waiting for your decision before proceeding.</p>
<div style="margin:24px 0;">
  <a href="{approve_url}" style="background:#16a34a;color:white;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:bold;margin-right:12px;">Approve</a>
  <a href="{reject_url}" style="background:#dc2626;color:white;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:bold;">Reject</a>
</div>
<p style="color:#64748b;font-size:12px;">Links expire in 24 hours. To approve/reject with notes, visit the <a href="{frontend_url}/approvals">Approval Inbox</a>.</p>
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
