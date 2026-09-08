"""SQLAlchemy ORM model for durably-stored guardrail rules (P1-4).

Rules used to live only in ``GuardrailsEngine._rules`` (an in-memory dict) and
vanished on restart. This table backs them so seeded defaults and API-created
rules survive a process restart. Tenant-scoped with RLS (see migration 0113).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class GuardrailRuleRow(Base):
    """One guardrail rule, mirroring app.guardrails_v2.models.GuardrailRule."""

    __tablename__ = "guardrail_rules"

    rule_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    rule_type: Mapped[str] = mapped_column(String(50), nullable=False)
    layers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    action: Mapped[str] = mapped_column(String(50), nullable=False, default="block")
    categories: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="high")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
