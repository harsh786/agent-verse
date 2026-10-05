"""ingestion_live_listings: staged upstream listings for deletion reconciliation (KB-44).

Every successful object-store sync listed the WHOLE bucket into a Python set
and walked every indexed document against it: O(objects) memory per sync. The
reconciler now streams the upstream listing into this table in bounded batches
(one ``run_id`` per run) and lets Postgres find the indexed documents that are
not listed (an indexed anti-join, keyset-paged), so process memory stays
bounded at any bucket size. Rows live only for one run (deleted at its end;
leftovers of a crashed run are purged by the tenant's next run).

UNLOGGED: the rows are scratch data rebuilt on every run, so they skip WAL (and
a crash truncating them only aborts that run). RLS enabled and FORCEd with the
standard tenant policy.

Revision ID: e3d7f9b1c5a2
Revises: e2e2811317df
Create Date: 2026-10-05
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e3d7f9b1c5a2"
down_revision: str | None = "e2e2811317df"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE UNLOGGED TABLE IF NOT EXISTS ingestion_live_listings (
            tenant_id  VARCHAR(255) NOT NULL,
            run_id     VARCHAR(64)  NOT NULL,
            doc_id     TEXT         NOT NULL,
            created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, run_id, doc_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_ingestion_live_listings_created "
        "ON ingestion_live_listings (tenant_id, created_at)"
    )
    op.execute("ALTER TABLE ingestion_live_listings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ingestion_live_listings FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS ingestion_live_listings_tenant_isolation "
        "ON ingestion_live_listings"
    )
    op.execute(
        "CREATE POLICY ingestion_live_listings_tenant_isolation ON ingestion_live_listings "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ingestion_live_listings")
