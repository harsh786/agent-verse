"""SQLAlchemy models for durable state-machine definitions and instances.

The state-machine registry (``app.triggers.state_machine.StateMachine``) was
in-memory only, so definitions and instances vanished on restart while the REST
API implied durability. These tables back that registry: definitions and their
running instances survive restarts and span workers. Tenant-isolated via RLS
like the other tenant tables; ``tenant_id`` is TEXT to match
``TenantContext.tenant_id``. States/transitions and per-instance history are
stored as JSONB (bounded, not a normalized schema).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class StateMachineDefinitionRow(Base):
    """A registered state-machine definition (states + transitions as JSONB)."""

    __tablename__ = "trigger_state_machine_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "machine_id", name="uq_tsm_def_tenant_machine"),
    )

    machine_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    definition: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StateMachineInstanceRow(Base):
    """A running instance of a state machine, keyed by (tenant_id, entity_id)."""

    __tablename__ = "trigger_state_machine_instances"
    __table_args__ = (
        UniqueConstraint("tenant_id", "entity_id", name="uq_tsm_inst_tenant_entity"),
    )

    instance_id: Mapped[str] = mapped_column(Text, primary_key=True)
    machine_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    current_state: Mapped[str] = mapped_column(Text, nullable=False)
    history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'")
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="running", server_default="running"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
