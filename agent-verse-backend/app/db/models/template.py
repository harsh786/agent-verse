"""SQLAlchemy model for parameterized goal templates."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class GoalTemplate(Base):
    """Reusable goal template with {{parameter}} placeholders.

    Example definition:
        goal_text = "Deploy {{service}} to {{environment}} with version {{tag}}"
        parameters = [{"name": "service", "description": "Service name", "required": True},
                      {"name": "environment", "description": "Target env", "required": True},
                      {"name": "tag", "description": "Docker tag", "default": "latest"}]
    """
    __tablename__ = "goal_templates"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    goal_text: Mapped[str] = mapped_column(Text, nullable=False)
    parameters: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'")
    )
    domain: Mapped[str] = mapped_column(String(100), nullable=False, default="general", server_default="general")
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
