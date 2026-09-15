"""Dual-mode identity — unified principals across interfaces (Phase 3 / Part B).

A *principal* is the human (or individual account) behind a conversation, whether
they arrive via web login, a WhatsApp number, a Telegram id, or an API actor. An
``identity_link`` maps ``(tenant, channel, channel_user_id) → principal``, so a
thread started on the web can continue on WhatsApp — or months later on any
channel — as the *same* principal with the same memory and personalization.

This makes "continue from where he left, from any interface" true regardless of
where the last message was sent, and gives standalone individuals a stable
identity distinct from the enterprise-tenant concept.
"""

from __future__ import annotations

from app.identity.models import IdentityLink, Principal, PrincipalKind
from app.identity.service import IdentityService, InMemoryIdentityStore

__all__ = [
    "IdentityLink",
    "IdentityService",
    "InMemoryIdentityStore",
    "Principal",
    "PrincipalKind",
]
