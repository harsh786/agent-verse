"""SQLAlchemy ORM models for dynamic orchestration persistence.

Tables:
  eval_scorecards          — per-goal eval results
  tool_trust_records       — per-tool trust history (persisted across restarts)
  self_improvement_actions — decisions made after goal completion
  ab_test_results          — A/B experiment arm results
  reflexion_lessons        — persistent failure lessons per tenant
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class EvalScorecard(Base):
    __tablename__ = "eval_scorecards"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    goal_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    overall_score: Mapped[float] = mapped_column(Float, nullable=False)
    scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="'{}'")
    improvement_suggestions: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    primary_strategy_id: Mapped[str] = mapped_column(Text, nullable=False, default="unknown")
    primary_strategy_version: Mapped[str] = mapped_column(Text, nullable=False, default="unknown")
    auxiliary_strategy_versions: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    strategy_execution_id: Mapped[str] = mapped_column(Text, nullable=False)
    evaluator_version: Mapped[str] = mapped_column(
        Text, nullable=False, default="runtime-scorecard-v2"
    )
    dimension_status: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    evidence_references: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    coverage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    correlation_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RegressionCase(Base):
    __tablename__ = "regression_cases"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    goal_id: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_id: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_version: Mapped[str] = mapped_column(Text, nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluator_version: Mapped[str] = mapped_column(Text, nullable=False)
    dataset_version: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RegressionBaseline(Base):
    __tablename__ = "regression_baselines"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    cohort: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_id: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_version: Mapped[str] = mapped_column(Text, nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    evaluator_version: Mapped[str] = mapped_column(Text, nullable=False)
    eval_suite_version: Mapped[str] = mapped_column(Text, nullable=False)
    limits_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReasoningPromotionDecision(Base):
    __tablename__ = "reasoning_promotion_decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    baseline_id: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy_id: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_version: Mapped[str] = mapped_column(Text, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    metric_deltas: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ToolTrustRecord(Base):
    __tablename__ = "tool_trust_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    tool_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    success_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class SelfImprovementAction(Base):
    __tablename__ = "self_improvement_actions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    goal_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    action_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ABTestResult(Base):
    __tablename__ = "ab_test_results"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    goal_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    experiment_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    arm_id: Mapped[str] = mapped_column(String(100), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReflexionLesson(Base):
    __tablename__ = "reflexion_lessons"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    lesson: Mapped[str] = mapped_column(Text, nullable=False)
    source_goal_id: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_class: Mapped[str] = mapped_column(String(100), nullable=False, default="unknown")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class StrategyCertificationEvidence(Base):
    __tablename__ = "strategy_certification_evidence"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    strategy_id: Mapped[str] = mapped_column(String(100), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    state_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(20), nullable=False)
    artifact_reference: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
