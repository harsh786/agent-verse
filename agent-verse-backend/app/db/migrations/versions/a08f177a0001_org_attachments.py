"""org_attachments: durable, tenant-scoped mission attachments (a08-F177-01).

Mission attachments were written to the API host's filesystem
(``ORG_ATTACHMENTS_DIR`` or the temp dir) and referenced by absolute path, so a
worker or another replica could not read them and nothing ever deleted them.
One row per attachment; bytes in ``content`` (the API caps them at 25 MB), RLS
forced, and ``expires_at`` drives the ``purge_expired_org_attachments`` beat task.

Revision ID: a08f177a0001
Revises: f1e790b4050a
"""

from __future__ import annotations

from alembic import op

revision = "a08f177a0001"
down_revision = "f1e790b4050a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS org_attachments (
            id            UUID         PRIMARY KEY,
            tenant_id     UUID         NOT NULL,
            org_id        UUID         NOT NULL REFERENCES organizations (id) ON DELETE CASCADE,
            filename      VARCHAR(255) NOT NULL,
            content_type  VARCHAR(255) NOT NULL,
            size_bytes    INTEGER      NOT NULL CHECK (size_bytes >= 0),
            sha256        CHAR(64)     NOT NULL,
            content       BYTEA        NOT NULL,
            uploaded_by   VARCHAR(200),
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
            expires_at    TIMESTAMPTZ  NOT NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_attachments_tenant_org "
        "ON org_attachments (tenant_id, org_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_attachments_expires ON org_attachments (expires_at)"
    )
    op.execute("ALTER TABLE org_attachments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE org_attachments FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS org_attachments_tenant_isolation ON org_attachments")
    op.execute(
        "CREATE POLICY org_attachments_tenant_isolation ON org_attachments "
        "USING (tenant_id::text = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS org_attachments")
