"""SQLAlchemy ORM models for governance: audit log, approval requests, policy versions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
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


class PolicyVersion(Base):
    """Immutable snapshot of a policy at a given version number (migration 0056)."""

    __tablename__ = "policy_versions"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules: Mapped[Any] = mapped_column(JSONB, nullable=False, default=list)
    metadata_: Mapped[Any] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parent_policy_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

