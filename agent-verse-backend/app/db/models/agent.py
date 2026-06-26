"""SQLAlchemy ORM models for agents and agent permissions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    goal_template: Mapped[str] = mapped_column(Text, nullable=False, default="")
    autonomy_mode: Mapped[str] = mapped_column(
        String(50), nullable=False, default="bounded-autonomous"
    )
    connector_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    trigger_config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    permissions: Mapped[list[AgentPermission]] = relationship(
        "AgentPermission", back_populates="agent", cascade="all, delete-orphan"
    )


class AgentPermission(Base):
    __tablename__ = "agent_permissions"
    __table_args__ = (UniqueConstraint("agent_id", "tool_name", name="uq_agent_tool"),)

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    agent_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    level: Mapped[str] = mapped_column(String(20), nullable=False, default="allow_log")
    daily_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    per_goal_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scope_pattern: Mapped[str | None] = mapped_column(String(500), nullable=True)

    agent: Mapped[Agent] = relationship("Agent", back_populates="permissions")
