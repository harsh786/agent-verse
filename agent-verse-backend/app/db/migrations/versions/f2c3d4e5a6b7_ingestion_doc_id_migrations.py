"""ingestion_doc_id_migrations: resumable per-Source document-id migrations (D2).

MongoDB document ids moved from host-based UUID v5 ids to UUID v8 (TG-13 /
TG-07). Documents indexed before keep their v5 id, and an updated one ends up
twice (the v8 copy next to the stale v5 one). A one-time, bounded reindex per
Source re-reads them, writes them under the v8 id and removes the stale copy;
this table holds its persisted cursor and tallies so it resumes where it
stopped on any worker, and so a finished Source is never scanned again.

RLS enabled and FORCEd with the standard tenant policy: every run acts for one
tenant under its RLS context (the dispatcher's cross-tenant listing runs on the
maintenance role).

Revision ID: f2c3d4e5a6b7
Revises: e1a2b3c4d5f6
"""

from __future__ import annotations

from alembic import op

revision = "f2c3d4e5a6b7"
down_revision = "e1a2b3c4d5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ingestion_doc_id_migrations (
            tenant_id    VARCHAR(255) NOT NULL,
            source_id    VARCHAR(255) NOT NULL,
            migration    VARCHAR(64)  NOT NULL,
            status       VARCHAR(32)  NOT NULL,
            cursor       TEXT,
            scanned      BIGINT       NOT NULL DEFAULT 0,
            migrated     BIGINT       NOT NULL DEFAULT 0,
            deleted      BIGINT       NOT NULL DEFAULT 0,
            held         BIGINT       NOT NULL DEFAULT 0,
            skipped      BIGINT       NOT NULL DEFAULT 0,
            failed       BIGINT       NOT NULL DEFAULT 0,
            last_error   TEXT,
            started_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
            completed_at TIMESTAMPTZ,
            PRIMARY KEY (tenant_id, source_id, migration)
        )
        """
    )
    op.execute("ALTER TABLE ingestion_doc_id_migrations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE ingestion_doc_id_migrations FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS ingestion_doc_id_migrations_tenant_isolation "
        "ON ingestion_doc_id_migrations"
    )
    op.execute(
        "CREATE POLICY ingestion_doc_id_migrations_tenant_isolation "
        "ON ingestion_doc_id_migrations "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ingestion_doc_id_migrations")
