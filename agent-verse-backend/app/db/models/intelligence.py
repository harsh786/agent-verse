"""SQLAlchemy ORM models for intelligence: decision traces, evaluations, cost ledger,
collaborative editing sessions, and agent templates."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.models import Base


class DecisionTrace(Base):
    """Records every reasoning step taken by the agent for explainability."""

    __tablename__ = "decision_traces"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    goal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    step_id: Mapped[str | None] = mapped_column(String(32), nullable=True, default="")
    action: Mapped[str] = mapped_column(Text, nullable=False)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    evidence: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    alternatives: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Evaluation(Base):
    """LLM-as-judge scores for a completed goal."""

    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    goal_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("goals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scores: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    average_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CostLedger(Base):
    """Per-call token and USD cost tracking for budget management."""

    __tablename__ = "cost_ledger"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    goal_id: Mapped[str | None] = mapped_column(String(32), nullable=True, default="")
    tool_name: Mapped[str | None] = mapped_column(String(200), nullable=True, default="")
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CollabSession(Base):
    """Real-time collaborative editing session (OT/CRDT model)."""

    __tablename__ = "collab_sessions"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    mode: Mapped[str | None] = mapped_column(String(50), nullable=True, default="suggest")
    status: Mapped[str | None] = mapped_column(String(20), nullable=True, default="active")
    content: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    operations: Mapped[list[CollabOperation]] = relationship(
        "CollabOperation", back_populates="session", cascade="all, delete-orphan"
    )


class CollabOperation(Base):
    """A single operational-transform operation within a collab session."""

    __tablename__ = "collab_operations"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    session_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("collab_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    operation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    author: Mapped[str | None] = mapped_column(String(200), nullable=True, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    session: Mapped[CollabSession] = relationship(
        "CollabSession", back_populates="operations"
    )


class AgentTemplate(Base):
    """Reusable agent blueprint — system templates have tenant_id = NULL."""

    __tablename__ = "agent_templates"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    # NULL = system/public template; non-NULL = tenant-owned template
    tenant_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(100), nullable=True, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    goal_template: Mapped[str] = mapped_column(Text, nullable=False)
    connectors: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    trigger_type: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default="rest"
    )
    autonomy_mode: Mapped[str | None] = mapped_column(
        String(50), nullable=True, default="bounded-autonomous"
    )
    is_public: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
