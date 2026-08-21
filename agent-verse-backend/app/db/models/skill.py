"""Skill entity — composable instruction packs for agents."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.db.models import Base


class Skill(Base):
    """A composable instruction pack that agents load based on goal relevance."""

    __tablename__ = "skills"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, index=True
    )  # null = platform skill
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trigger_hints: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    few_shot_examples: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    allowed_tools: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    required_connectors: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    token_estimate: Mapped[int] = mapped_column(nullable=False, default=0)
    visibility: Mapped[str] = mapped_column(
        String(32), nullable=False, default="tenant"
    )  # platform|tenant|marketplace
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
