"""User login sessions (SAML / Google / other browser SSO).

A session is the credential a person holds after an interactive SSO login. The
raw bearer token (``avs_…``) is never stored — only its SHA-256 — and the row is
created *before* the token exists: the login flow first records a one-time
``login_code_hash`` (60 s) that the browser exchanges for the token, so the
token itself never travels in a redirect URL.

Postgres is the source of truth (FORCE RLS on ``tenant_id``); Redis only caches
resolved contexts for a short TTL and is purged on revocation.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class UserSession(Base):
    __tablename__ = "user_sessions"
    __table_args__ = (
        Index("ix_user_sessions_tenant_user", "tenant_id", "user_id"),
        Index("ix_user_sessions_user_id", "user_id"),
        Index("ix_user_sessions_expires_at", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # SHA-256 of the bearer token; NULL until the login code is exchanged.
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    # SHA-256 of the one-time login code; cleared when exchanged.
    login_code_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    login_code_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    auth_method: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
