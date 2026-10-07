"""Durable rows for state that used to live in per-process memory.

Schema owner: migration ``f7a8b9c0d1e2``. The stores write these tables with raw
SQL under ``app.db.rls.sqlalchemy_rls_context`` (see
``app/observability/cost_breakdown.py`` and ``app/enterprise/simulation.py``);
the ORM classes document the schema and are available for queries/tests.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class GoalCostBreakdownRow(Base):
    """Accumulated tokens/cost of one (goal, role, model) — additive UPSERT target."""

    __tablename__ = "goal_cost_breakdowns"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    goal_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), primary_key=True)
    model: Mapped[str] = mapped_column(String(255), primary_key=True, server_default="")
    input_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    calls: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Models the role's calls failed over FROM before ``model`` served them
    # (migration e1f3a5c7b9d2); ``model`` is always the serving model.
    fallback_from: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    first_recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SimulationRunRow(Base):
    """One enterprise simulation run; ``payload`` is the serialized SimulationRun."""

    __tablename__ = "simulation_runs"

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class A2ARemoteAgentRow(Base):
    """A tenant's registered remote A2A agent (schema owner: ``5ea3dc985432``).

    Written with raw SQL under RLS by ``app/api/a2a_remote_agents.py``.
    """

    __tablename__ = "a2a_remote_agents"
    __table_args__ = (UniqueConstraint("tenant_id", "url", name="uq_a2a_remote_agents_tenant_url"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    card: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
