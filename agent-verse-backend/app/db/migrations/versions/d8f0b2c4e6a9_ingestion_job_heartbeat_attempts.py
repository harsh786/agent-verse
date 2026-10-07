"""Sync job heartbeat, lease token, attempt count and requeue reason (SYNC-ORPHAN).

A knowledge-Source sync whose worker died stayed ``running`` until the
age-based stale-job reaper failed it two hours after it started, and it was
never run again. A running sync now heart-beats its job row; orphan recovery
requeues a job whose heartbeat went stale (and whose Source lock expired) as
the same job, resuming from its checkpoint, at most
``ingestion_sync_max_attempts`` times:

* ``lease_token``    -- the sync lock value of the run that owns the job
  (compare-and-set fencing between a recovering requeue and a live run);
* ``heartbeat_at``   -- last heartbeat of that run;
* ``attempts``       -- runs of the job started so far (served by the API);
* ``requeue_reason`` -- why it was last requeued (served by the API).

Additive only: nullable columns or constant defaults (no table rewrite on
Postgres 11+), plus a small partial index over the active rows the recovery
scan reads. Existing rows keep ``lease_token`` NULL and stay with the age-based
reaper.

Revision ID: d8f0b2c4e6a9
Revises: a9c1e3f5b7d4
Create Date: 2026-10-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8f0b2c4e6a9"
down_revision: str | Sequence[str] | None = "a9c1e3f5b7d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '5s';")
    op.add_column("ingestion_jobs", sa.Column("lease_token", sa.String(160), nullable=True))
    op.add_column(
        "ingestion_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("attempts", sa.Integer, nullable=False, server_default="1"),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("requeue_reason", sa.Text, nullable=False, server_default=""),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_active_heartbeat "
        "ON ingestion_jobs (heartbeat_at) WHERE status IN ('pending', 'running')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_ingestion_jobs_active_heartbeat")
    op.drop_column("ingestion_jobs", "requeue_reason")
    op.drop_column("ingestion_jobs", "attempts")
    op.drop_column("ingestion_jobs", "heartbeat_at")
    op.drop_column("ingestion_jobs", "lease_token")
