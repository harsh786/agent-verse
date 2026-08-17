"""0110 — ingestion schema hardening.

Adds:
  - source_configs.consecutive_failures  (backoff counter)
  - ingestion_dlq.dlq_id                 (consistent primary key alias)
  - ingestion_dlq.raw_doc_json           (serialized doc for retry)
  - ingestion_dlq.permanent_failure      (no more retries flag)
  - ingestion_dlq.last_error             (last retry error message)
  - ingestion_dlq.last_retried_at        (timestamp of last retry attempt)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0110"
down_revision = "0109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── source_configs additions ───────────────────────────────────────────────
    op.add_column(
        "source_configs",
        sa.Column("consecutive_failures", sa.Integer, nullable=False, server_default="0"),
    )

    # ── ingestion_dlq additions ────────────────────────────────────────────────
    # Add dlq_id as a stable UUID column (the existing 'id' column is the PK)
    op.add_column(
        "ingestion_dlq",
        sa.Column("dlq_id", sa.String(64), nullable=True),
    )
    # Back-fill dlq_id with existing id values
    op.execute("UPDATE ingestion_dlq SET dlq_id = id WHERE dlq_id IS NULL")

    op.add_column(
        "ingestion_dlq",
        sa.Column("raw_doc_json", sa.Text, nullable=True),
    )
    op.add_column(
        "ingestion_dlq",
        sa.Column("permanent_failure", sa.Boolean, nullable=False, server_default="false"),
    )
    op.add_column(
        "ingestion_dlq",
        sa.Column("last_error", sa.Text, nullable=True),
    )
    op.add_column(
        "ingestion_dlq",
        sa.Column("last_retried_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_ingestion_dlq_retryable",
        "ingestion_dlq",
        ["permanent_failure", "retry_count", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_ingestion_dlq_retryable", "ingestion_dlq")
    op.drop_column("ingestion_dlq", "last_retried_at")
    op.drop_column("ingestion_dlq", "last_error")
    op.drop_column("ingestion_dlq", "permanent_failure")
    op.drop_column("ingestion_dlq", "raw_doc_json")
    op.drop_column("ingestion_dlq", "dlq_id")
    op.drop_column("source_configs", "consecutive_failures")
