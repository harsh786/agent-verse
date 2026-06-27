"""SQLAlchemy ORM models for Agent Civilization tables."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class Civilization(Base):
    __tablename__ = "civilizations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    constitution: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CivilizationAgent(Base):
    __tablename__ = "civilization_agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    civilization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("civilizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[str] = mapped_column(
        String(50), nullable=False, default="worker", server_default="worker"
    )
    parent_agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reputation: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5, server_default="0.5"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    budget_usd: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    budget_spent_usd: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )
    spawned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_active_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SpawnRequest(Base):
    __tablename__ = "spawn_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    civilization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("civilizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    requester_agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_capability: Mapped[str] = mapped_column(Text, nullable=False)
    goal_text: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    verdict: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BlackboardEntry(Base):
    __tablename__ = "blackboard_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    civilization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("civilizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    author_agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    topic: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.8, server_default="0.8"
    )
    refs: Mapped[list] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BusMessage(Base):
    __tablename__ = "bus_messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    civilization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("civilizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    from_agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    topic: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )


class CivilizationLearning(Base):
    __tablename__ = "civilization_learnings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    civilization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("civilizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    candidate: Mapped[str] = mapped_column(Text, nullable=False)
    source_agent_id: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="candidate", server_default="candidate", index=True
    )
    eval_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    promoted_memory_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CivilizationEvent(Base):
    __tablename__ = "civilization_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    civilization_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("civilizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
