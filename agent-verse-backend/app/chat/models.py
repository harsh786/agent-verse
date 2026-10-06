"""SQLAlchemy ORM models for the chat feature."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base


def _hex_id() -> str:
    return uuid.uuid4().hex


class ChatSessionFolder(Base):
    __tablename__ = "chat_session_folders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_hex_id)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    color: Mapped[str] = mapped_column(String(20), nullable=False, default="#6366f1")
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # The principal that owns the folder (CHAT-D-1, migration f3a9c1e7d5b4), like
    # ``ChatSession.owner_principal``: only it lists, renames, deletes or files
    # into the folder.
    owner_principal: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    sessions: Mapped[list[ChatSession]] = relationship(
        "ChatSession", back_populates="folder", foreign_keys="ChatSession.folder_id"
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_hex_id)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="New Chat")
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ttl_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    folder_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("chat_session_folders.id", ondelete="SET NULL"), nullable=True
    )
    show_reasoning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    proactive_suggestions: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    preferred_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The person who created the session (CHAT-KB, migration c3e8a1f5b7d2); None
    # for an API key, a channel or an older session — never indexed as knowledge.
    owner_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The principal that owns it (CHAT-SEC-1, migration d4f2b8c6a9e1):
    # ``user:<id>`` or ``key:<api key id>``; None = no owner (admin routes only).
    owner_principal: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    folder: Mapped[ChatSessionFolder | None] = relationship(
        "ChatSessionFolder", back_populates="sessions"
    )
    messages: Mapped[list[ChatMessage]] = relationship(
        "ChatMessage", back_populates="session", order_by="ChatMessage.created_at"
    )
    usage: Mapped[list[ChatMessageUsage]] = relationship(
        "ChatMessageUsage", back_populates="session"
    )
    artifacts: Mapped[list[ChatArtifact]] = relationship("ChatArtifact", back_populates="session")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_hex_id)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # user|assistant|system
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # NOTE: the Python attribute is named ``metadata_`` (not ``metadata``) because
    # ``metadata`` is reserved by SQLAlchemy's Declarative API (it collides with
    # ``Base.metadata``, the schema MetaData object) and defining a column with
    # that attribute name raises ``InvalidRequestError`` at class-definition time.
    # The underlying DB column is still named "metadata" (see migrations 0105/0130).
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    branch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parent_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    goal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    intent: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped[ChatSession] = relationship("ChatSession", back_populates="messages")


class ChatMessageFeedback(Base):
    """One person's feedback on one chat reply (CHAT-D-2, migration a6c2e8f4b0d3)."""

    __tablename__ = "chat_message_feedback"
    __table_args__ = (
        UniqueConstraint("message_id", "owner_principal", name="uq_chat_message_feedback_rater"),
        CheckConstraint("rating BETWEEN -1 AND 1", name="ck_chat_message_feedback_rating"),
        CheckConstraint(
            "comment IS NULL OR char_length(comment) <= 4000",
            name="ck_chat_message_feedback_comment",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_hex_id)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chat_messages.id", ondelete="CASCADE"), nullable=False
    )
    owner_principal: Mapped[str] = mapped_column(String(256), nullable=False)
    rating: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ChatMessageUsage(Base):
    __tablename__ = "chat_message_usage"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_hex_id)
    message_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped[ChatSession] = relationship("ChatSession", back_populates="usage")


class ChatArtifact(Base):
    __tablename__ = "chat_artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_hex_id)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    language: Mapped[str] = mapped_column(String(50), nullable=False, default="text")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    session: Mapped[ChatSession] = relationship("ChatSession", back_populates="artifacts")
