"""SQLAlchemy ORM models for goals and goal steps."""

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    parent_goal_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("goals.id", ondelete="SET NULL"), nullable=True
    )
    goal_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="planning")
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="normal")
    autonomy_mode: Mapped[str] = mapped_column(
        String(50), nullable=False, default="bounded-autonomous"
    )
    workflow_mode: Mapped[str] = mapped_column(
        String(40), nullable=False, default="single_agent", server_default="single_agent"
    )
    execution_context: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    verification_feedback: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    steps: Mapped[list[GoalStep]] = relationship(
        "GoalStep",
        back_populates="goal",
        cascade="all, delete-orphan",
        order_by="GoalStep.step_index",
    )
    # Self-referential adjacency list for goal trees
    sub_goals: Mapped[list[Goal]] = relationship(
        "Goal",
        foreign_keys="[Goal.parent_goal_id]",
        back_populates="parent_goal",
    )
    parent_goal: Mapped[Goal | None] = relationship(
        "Goal",
        foreign_keys="[Goal.parent_goal_id]",
        back_populates="sub_goals",
        remote_side="[Goal.id]",
    )


class GoalStep(Base):
    __tablename__ = "goal_steps"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    goal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False)
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    output: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    goal: Mapped[Goal] = relationship("Goal", back_populates="steps")


class GoalEvent(Base):
    __tablename__ = "goal_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "goal_id", "sequence", name="uq_goal_events_sequence"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    goal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class GoalCheckpoint(Base):
    """Durable checkpoint payloads for future worker resume support."""

    __tablename__ = "goal_checkpoints"
    __table_args__ = (
        UniqueConstraint("tenant_id", "goal_id", "checkpoint_key", name="uq_goal_checkpoints_key"),
    )

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    goal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    checkpoint_key: Mapped[str] = mapped_column(String(120), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    checkpoint_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    recovery_status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="not_implemented", server_default="not_implemented"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
