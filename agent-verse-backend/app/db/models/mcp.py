"""SQLAlchemy ORM models for MCP servers, credentials, and OAuth tokens."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base


class MCPServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    auth_type: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    credentials: Mapped[list[MCPCredential]] = relationship(
        "MCPCredential", back_populates="server", cascade="all, delete-orphan"
    )
    oauth_tokens: Mapped[list[OAuthToken]] = relationship(
        "OAuthToken", back_populates="server", cascade="all, delete-orphan"
    )


class MCPCredential(Base):
    """Encrypted (AES-256-GCM Fernet) MCP server credentials."""

    __tablename__ = "mcp_credentials"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    server_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    encrypted_config: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    server: Mapped[MCPServer] = relationship("MCPServer", back_populates="credentials")


class OAuthToken(Base):
    """Encrypted OAuth access and refresh tokens for an MCP server."""

    __tablename__ = "oauth_tokens"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    server_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str | None] = mapped_column(String(50), nullable=True, default="Bearer")
    scope: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    server: Mapped[MCPServer] = relationship("MCPServer", back_populates="oauth_tokens")


class ConnectorHealthSnapshot(Base):
    __tablename__ = "connector_health_snapshots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True,
        default=lambda: uuid.uuid4().hex)
    server_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    __table_args__ = (
        Index("ix_ch_snapshots_server_tenant", "server_id", "tenant_id"),
    )
