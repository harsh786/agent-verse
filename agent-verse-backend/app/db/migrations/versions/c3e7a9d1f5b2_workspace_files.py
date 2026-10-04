"""workspace_files: durable tenant file workspace (NATIVE-01).

The ``/tools/files`` workspace lived in pod-local ``/tmp``: invisible to other
replicas and workers, lost on restart. One row per file or directory, keyed by
``(tenant_id, dir, name)`` so a directory listing is an indexed range read.

Revision ID: c3e7a9d1f5b2
Revises: f1e790b4050a
"""

from __future__ import annotations

from alembic import op

revision = "c3e7a9d1f5b2"
down_revision = "f1e790b4050a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_files (
            tenant_id   VARCHAR(64)   NOT NULL,
            dir         VARCHAR(1024) NOT NULL,
            name        VARCHAR(255)  NOT NULL,
            kind        VARCHAR(16)   NOT NULL,
            content     TEXT,
            size_bytes  BIGINT        NOT NULL DEFAULT 0,
            sha256      CHAR(64),
            created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            PRIMARY KEY (tenant_id, dir, name),
            CONSTRAINT ck_workspace_files_kind CHECK (kind IN ('file', 'directory'))
        )
        """
    )
    # Subtree deletes (dir LIKE 'a/b/%') under any collation.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workspace_files_dir_prefix "
        "ON workspace_files (tenant_id, dir varchar_pattern_ops)"
    )
    op.execute("ALTER TABLE workspace_files ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE workspace_files FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS workspace_files_tenant_isolation ON workspace_files")
    op.execute(
        "CREATE POLICY workspace_files_tenant_isolation ON workspace_files "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS workspace_files")
