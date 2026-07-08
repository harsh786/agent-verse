"""ORM models for episodic and procedural memory."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class EpisodicMemory(Base):
    """Stores past goal execution experiences for recall during planning."""
    __tablename__ = "episodic_memories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                     default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    goal_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    goal_text: Mapped[str] = mapped_column(Text, nullable=False)
    # What the agent decided to do
    action_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # What happened
    outcome: Mapped[str] = mapped_column(String(50), nullable=False, default="success")  # success|failed|partial
    # Key lessons learned from this episode
    lessons: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Embedding of goal_text for semantic recall (optional)
    embedding: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    # Quality score (0–1)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    # Number of steps taken
    steps_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Tool calls made
    tools_used: Mapped[list[str]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProceduralMemory(Base):
    """Stores learned tool-use patterns (skills) for goal types."""
    __tablename__ = "procedural_memories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True,
                                     default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # What kind of goal this skill applies to
    goal_pattern: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    # Domain / category
    domain: Mapped[str] = mapped_column(String(50), nullable=False, default="general", index=True)
    # The learned tool sequence (ordered list)
    tool_sequence: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    # How many times this skill was applied
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Success rate (0–1)
    success_rate: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    # Average steps saved vs. naive approach
    avg_steps_saved: Mapped[float] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
