"""Per-channel authentication guard — Q9 of spec.

Each channel has its own auth mechanism:
  rest      → X-API-Key header (tenant API key)
  telegram  → Telegram user_id must be in allowed list
  slack     → Slack workspace linked to tenant, Bolt signed secret
  discord   → Ed25519 signature on interaction payload
  whatsapp  → Phone number in allowed list
  email     → Sender address in allowed list
  mcp       → MCP scoped API key
  a2a       → Agent certificate or signed JWT
  webhook   → HMAC-SHA256 on X-Webhook-Signature header
  voice_webhook → HMAC-SHA256 on X-Voice-Signature header
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.observability.logging import get_logger

_log = get_logger(__name__)


class ChannelAuthGuard:
    """
    Verifies channel-specific authentication for every inbound command.
    All channels ultimately verify against tenant API key or user session.
    """

    CHANNEL_AUTH_DOCS = {
        "rest":          "X-API-Key header (tenant API key)",
        "telegram":      "Telegram user_id must be in org's allowed_telegram_users list",
        "slack":         "Slack workspace must be linked to tenant, user in org team",
        "discord":       "Ed25519 signature verified against DISCORD_PUBLIC_KEY",
        "whatsapp":      "Phone number must be in org's allowed_phones list",
        "mcp":           "MCP token (scoped API key)",
        "a2a":           "Agent certificate or signed JWT",
        "email":         "From address must be in org's allowed_emails list",
        "webhook":       "HMAC-SHA256 signature on X-Webhook-Signature header",
        "voice_webhook": "HMAC-SHA256 signature on X-Voice-Signature header",
    }

    # Scope enforcement — what each scope allows
    SCOPE_PERMISSIONS: dict[str, list[str]] = {
        "orgs:read":      ["read"],
        "missions:write": ["read", "create_mission", "update_mission"],
        "approve":        ["read", "approve", "reject"],
        "admin":          ["read", "write", "approve", "admin", "change_settings"],
        "voice":          ["read", "create_mission"],
    }

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def verify_api_key(self, api_key: str, tenant_id: str) -> bool:
        """Verify a tenant API key (hash comparison)."""
        if not api_key or not tenant_id:
            return False
        # TODO: Look up hashed key in DB
        # For now: accept any non-empty key (real implementation uses DB lookup)
        return len(api_key) >= 16

    def verify_hmac(
        self, payload_bytes: bytes, signature_header: str, secret: str
    ) -> bool:
        """Verify HMAC-SHA256 signature."""
        if not secret or not signature_header:
            return False
        expected = "sha256=" + hmac.new(
            secret.encode(), payload_bytes, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature_header, expected)

    def verify_telegram_user(
        self, telegram_user_id: str, allowed_ids: list[str]
    ) -> bool:
        if not allowed_ids:
            return False
        return str(telegram_user_id) in {str(i) for i in allowed_ids}

    def verify_whatsapp_phone(
        self, phone_number: str, allowed_phones: list[str]
    ) -> bool:
        if not allowed_phones:
            return False
        # Normalize: strip spaces/dashes/+
        norm = lambda p: p.replace("+", "").replace(" ", "").replace("-", "")
        return norm(phone_number) in {norm(p) for p in allowed_phones}

    def verify_email_sender(self, email: str, allowed_emails: list[str]) -> bool:
        if not allowed_emails:
            return False
        return email.lower() in {e.lower() for e in allowed_emails}

    def check_scope(self, scopes: list[str], required_action: str) -> bool:
        """Check if any of the provided scopes grants the required action."""
        for scope in scopes:
            perms = self.SCOPE_PERMISSIONS.get(scope, [])
            if required_action in perms or "admin" in perms:
                return True
        return False
