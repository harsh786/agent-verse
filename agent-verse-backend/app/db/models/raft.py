"""Durable tenant-scoped records for the RAFT lifecycle."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models import Base


class RAFTDataset(Base):
    __tablename__ = "raft_datasets"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_raft_datasets_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ("tenant_id", "collection_id"),
            ("knowledge_collections.tenant_id", "knowledge_collections.id"),
            name="fk_raft_datasets_tenant_collection",
            ondelete="CASCADE",
        ),
        Index(
            "idx_raft_datasets_tenant_collection",
            "tenant_id",
            "collection_id",
        ),
        CheckConstraint(
            "length(content_fingerprint) = 64",
            name="ck_raft_datasets_content_fingerprint",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    collection_id: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    examples: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    validation_errors: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class RAFTFineTuneJob(Base):
    __tablename__ = "raft_fine_tune_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "dataset_id"),
            ("raft_datasets.tenant_id", "raft_datasets.id"),
            name="fk_raft_jobs_tenant_dataset",
            ondelete="CASCADE",
        ),
        Index(
            "idx_raft_jobs_tenant_dataset",
            "tenant_id",
            "dataset_id",
        ),
        CheckConstraint(
            "status IN ('pending', 'reconciling', 'submitted', 'running', "
            "'completed', 'failed')",
            name="ck_raft_jobs_status",
        ),
        CheckConstraint("version >= 0", name="ck_raft_jobs_version_nonnegative"),
        CheckConstraint(
            "estimated_cost IS NULL OR (estimated_cost >= 0 AND "
            "estimated_cost::text NOT IN ('NaN', 'Infinity', '-Infinity'))",
            name="ck_raft_jobs_cost_nonnegative",
        ),
        CheckConstraint(
            "(cost_currency IS NULL) = (estimated_cost IS NULL)",
            name="ck_raft_jobs_cost_fields_paired",
        ),
        CheckConstraint(
            "cost_currency IS NULL OR cost_currency ~ '^[A-Z]{3}$'",
            name="ck_raft_jobs_currency",
        ),
        CheckConstraint(
            "length(compatibility_key) = 64 AND length(confirmation_digest) = 64",
            name="ck_raft_jobs_durable_hashes",
        ),
        CheckConstraint(
            "status != 'completed' OR fine_tuned_model IS NOT NULL",
            name="ck_raft_jobs_completed_model",
        ),
        CheckConstraint(
            "status NOT IN ('submitted', 'running', 'completed') "
            "OR provider_job_id IS NOT NULL",
            name="ck_raft_jobs_provider_identity",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset_id: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        index=True,
    )
    collection_id: Mapped[str] = mapped_column(
        String(32), nullable=False, index=True
    )
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    base_model: Mapped[str] = mapped_column(String(200), nullable=False)
    capability: Mapped[str] = mapped_column(String(100), nullable=False)
    compatibility_key: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    confirmation_digest: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    version: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")
    provider_job_id: Mapped[str | None] = mapped_column(String(200))
    fine_tuned_model: Mapped[str | None] = mapped_column(String(200))
    evaluation: Mapped[dict[str, float] | None] = mapped_column(JSONB)
    cost_currency: Mapped[str | None] = mapped_column(String(8))
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class RAFTConfirmationGrant(Base):
    __tablename__ = "raft_confirmation_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ("tenant_id", "dataset_id"),
            ("raft_datasets.tenant_id", "raft_datasets.id"),
            name="fk_raft_grants_tenant_dataset",
            ondelete="CASCADE",
        ),
        Index(
            "idx_raft_grants_tenant_dataset",
            "tenant_id",
            "dataset_id",
        ),
        CheckConstraint(
            "estimated_amount >= 0 AND "
            "estimated_amount::text NOT IN ('NaN', 'Infinity', '-Infinity')",
            name="ck_raft_grants_amount_nonnegative",
        ),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_raft_grants_currency"),
        CheckConstraint(
            "length(token_hash) = 64 AND length(binding_digest) = 64",
            name="ck_raft_grants_durable_hashes",
        ),
    )

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset_id: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    base_model: Mapped[str] = mapped_column(String(200), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    estimated_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    binding_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = ["RAFTConfirmationGrant", "RAFTDataset", "RAFTFineTuneJob"]
