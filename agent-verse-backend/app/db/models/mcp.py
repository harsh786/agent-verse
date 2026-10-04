"""SQLAlchemy ORM models for MCP servers, credentials, and OAuth tokens."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class MCPServer(Base):
    """A tenant's connector — the durable source of truth for the MCP registry.

    ``config`` holds the full ``MCPServerConfig`` JSON; the other columns are the
    fields queries need. ``name_key`` (normalised display name) is unique per
    tenant. Redis only caches these rows (migration a7c4e2f9d1b3).
    """

    __tablename__ = "mcp_servers"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(255), primary_key=True, default=lambda: uuid.uuid4().hex)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_key: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    auth_type: Mapped[str] = mapped_column(String(50), nullable=False, default="none")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True, default="active")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    builtin_type: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    __table_args__ = (
        Index("uq_mcp_servers_tenant_name_key", "tenant_id", "name_key", unique=True),
    )


class MCPCredential(Base):
    """One encrypted connector secret (vault ciphertext; ``tv1:`` = tenant key)."""

    __tablename__ = "mcp_credentials"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    server_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    secret_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class MCPBuiltinProvisioning(Base):
    """Per-tenant marker of the built-in catalogue already provisioned."""

    __tablename__ = "mcp_builtin_provisioning"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    builtin_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConnectorStoreBackfill(Base):
    """Record of a completed one-time Redis -> Postgres connector copy."""

    __tablename__ = "connector_store_backfills"

    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class OAuthToken(Base):
    """Encrypted OAuth access and refresh tokens for an MCP server."""

    __tablename__ = "oauth_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    # No FK to mcp_servers (dropped in f1a2b3c4d5e7): unique (tenant_id, server_id).
    server_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    access_token_enc: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str | None] = mapped_column(String(50), nullable=True, default="Bearer")
    scope: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConnectorHealthSnapshot(Base):
    __tablename__ = "connector_health_snapshots"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
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
        # HEALTH-06 (migration b7d3e1f0a9c2): retention prune + history reads.
        Index("ix_ch_snapshots_checked_at", "checked_at"),
        Index(
            "ix_ch_snapshots_tenant_server_checked",
            "tenant_id",
            "server_id",
            checked_at.desc(),
        ),
    )
