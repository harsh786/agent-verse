"""SQLAlchemy ORM models for governance policies and trigger schedules."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class Policy(Base):
    """Tenant-scoped governance policy: lists denied and approval-gated tools."""

    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    denied_tools: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    approval_tools: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    scope: Mapped[str | None] = mapped_column(String(50), nullable=True, default="global")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Schedule(Base):
    """Agent trigger schedule (cron, interval, webhook, one-shot, or event)."""

    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    goal_id_template: Mapped[str] = mapped_column(String(500), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False)
    cron_expression: Mapped[str | None] = mapped_column(
        String(200), nullable=True, default=""
    )
    timezone: Mapped[str | None] = mapped_column(String(100), nullable=True, default="UTC")
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    webhook_token: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    event_channel: Mapped[str | None] = mapped_column(
        String(200), nullable=True, default=""
    )
    fire_at_iso: Mapped[str | None] = mapped_column(String(100), nullable=True, default="")
    condition: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_fired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_fire_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
