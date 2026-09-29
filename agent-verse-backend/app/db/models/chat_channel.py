"""Durable channel-user / principal -> chat-session mappings.

Schema owner: migration ``e5c1a9d3b7f2``. ``PostgresChatRepository`` writes these
tables with raw SQL under ``app.db.rls.sqlalchemy_rls_context``
(``resolve_channel_session`` / ``claim_principal_session``); the ORM classes
document the schema and are available for queries/tests.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class ChatChannelSession(Base):
    """One external chat (tenant, channel, channel_user_id) -> its chat session."""

    __tablename__ = "chat_channel_sessions"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    channel: Mapped[str] = mapped_column(Text, primary_key=True)
    channel_user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    chat_session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChatPrincipalSession(Base):
    """A linked-identity principal -> its open chat thread (cross-channel continuity)."""

    __tablename__ = "chat_principal_sessions"

    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True)
    principal_id: Mapped[str] = mapped_column(Text, primary_key=True)
    chat_session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
