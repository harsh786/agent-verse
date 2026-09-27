"""SQLAlchemy ORM models for eval suites and run results."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class EvalSuite(Base):
    """An eval suite containing golden test tasks."""

    __tablename__ = "eval_suites"

    # Keyed per tenant: suite ids are caller-chosen, so a global key let one
    # tenant probe for (and collide with) another tenant's ids.
    tenant_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    tasks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EvalSuiteRunResult(Base):
    """Results of running an eval suite against live agents."""

    __tablename__ = "eval_suite_results"
    __table_args__ = (
        Index("ix_eval_suite_results_tenant_suite_run", "tenant_id", "suite_id", "run_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(32), primary_key=True, index=True)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    suite_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(32), nullable=False)
    total_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pass_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    task_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    # running | completed | failed (reads report a stale "running" as abandoned)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="completed")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
