"""SQLAlchemy ORM models for governance: audit log and approval requests."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class AuditLog(Base):
    """Append-only audit trail — immutability enforced by DB trigger."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    # No FK to tenants — audit entries survive tenant deletion
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    goal_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False)
    action_level: Mapped[str] = mapped_column(String(20), nullable=False)
    outcome: Mapped[str] = mapped_column(String(100), nullable=False)
    step_id: Mapped[str | None] = mapped_column(String(32), nullable=True, default="")
    approver: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ApprovalRequest(Base):
    """Human-in-the-loop approval gate for high-risk agent actions."""

    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    goal_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False, default="medium")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    approver: Mapped[str | None] = mapped_column(String(200), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
