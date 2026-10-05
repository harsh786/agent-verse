"""SQLAlchemy ORM models for eval suites and run results."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Float, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class EvalSuite(Base):
    """An eval suite containing golden test tasks."""

    __tablename__ = "eval_suites"

    # Keyed per tenant: suite ids are caller-chosen, so a global key let one
    # tenant probe for (and collide with) another tenant's ids.
    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # Bumped by every golden-task add / edit / delete / import (MEM-54). The
    # tasks themselves are revision rows in ``golden_tasks``.
    dataset_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
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

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: uuid.uuid4().hex)
    suite_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    total_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    passed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_tasks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pass_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    task_results: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    # running | completed | failed (reads report a stale "running" as abandoned)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="completed")
    # The suite dataset version the run executed (MEM-54).
    dataset_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # The agent the golden goals ran on and its behaviour-config hash (MEM-52).
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GoldenTaskRevision(Base):
    """One revision of a golden task, valid for dataset versions [valid_from, valid_to).

    Copy-on-write (MEM-54): an edit closes the current revision and inserts a
    new one; a delete only closes it. A trigger keeps closed revisions immutable.
    """

    __tablename__ = "golden_tasks"
    __table_args__ = (
        Index(
            "ix_golden_tasks_suite_position", "tenant_id", "eval_suite_id", "position", "task_id"
        ),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(Text, nullable=False)
    eval_suite_id: Mapped[str] = mapped_column(Text, nullable=False)
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    expected_phrases: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    expected_tool_calls: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    forbidden_tools: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    expected_output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    min_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.8)
    max_iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=15)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    valid_from: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    valid_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
