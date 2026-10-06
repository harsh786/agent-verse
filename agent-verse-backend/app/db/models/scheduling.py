"""SQLAlchemy ORM models for governance policies and trigger schedules."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class Policy(Base):
    """Tenant-scoped governance policy: lists denied and approval-gated tools."""

    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
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
    # The beat's due scan (TRG-15, migration e2b4d6f8a0c1).
    __table_args__ = (
        Index(
            "ix_schedules_due",
            "tenant_id",
            "next_fire_at",
            postgresql_where=text("NOT paused"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    goal_id_template: Mapped[str] = mapped_column(String(500), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(20), nullable=False)
    cron_expression: Mapped[str | None] = mapped_column(String(200), nullable=True, default="")
    timezone: Mapped[str | None] = mapped_column(String(100), nullable=True, default="UTC")
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)
    webhook_token: Mapped[str | None] = mapped_column(String(64), nullable=True, default="")
    event_channel: Mapped[str | None] = mapped_column(String(200), nullable=True, default="")
    fire_at_iso: Mapped[str | None] = mapped_column(String(100), nullable=True, default="")
    condition: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    # Family-specific config carried to the beat loop (file_watch_path, rss_url,
    # poll_url, …) — see app.triggers.store.spec_config (migration 0116). Declared
    # JSONB to match the actual column type migration 0116 creates, so alembic
    # autogenerate doesn't propose a spurious type change and JSONB operators/GIN
    # indexes are available.
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Webhook signing secrets, Fernet-encrypted (app.providers.vault) — migration
    # b3c4d5e6f7a9. Previously there was no column at all, so a trigger's secret
    # lived only in one process's memory and a restart made it accept unsigned
    # deliveries. The previous secret stays valid until the grace deadline.
    webhook_signature_secret_enc: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    webhook_signature_secret_prev_enc: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    webhook_secret_grace_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When the trigger was last (re)armed: resumed or its spec edited (B1-1,
    # migration d4f6b8a0c2e3). The beat never fires a slot at or before
    # ``armed_at or created_at``, so a new, resumed or re-timed schedule does not
    # replay slots from before it existed / while it was paused.
    armed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
