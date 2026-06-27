# app/db/migrations/versions/0036_artifacts_retention.py
"""Create artifacts table with retention policy support."""
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS artifacts (
            id           TEXT        PRIMARY KEY,
            tenant_id    TEXT        NOT NULL,
            goal_id      TEXT        NOT NULL DEFAULT '',
            filename     TEXT        NOT NULL,
            content_type TEXT        NOT NULL DEFAULT 'application/octet-stream',
            size_bytes   BIGINT      NOT NULL DEFAULT 0,
            artifact_url TEXT        NOT NULL DEFAULT '',
            expires_at   TIMESTAMPTZ,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_artifacts_tenant ON artifacts (tenant_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_artifacts_goal ON artifacts (goal_id) WHERE goal_id != ''")
    op.execute("CREATE INDEX IF NOT EXISTS ix_artifacts_expires ON artifacts (expires_at) WHERE expires_at IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_artifacts_expires")
    op.execute("DROP INDEX IF EXISTS ix_artifacts_goal")
    op.execute("DROP INDEX IF EXISTS ix_artifacts_tenant")
    op.execute("DROP TABLE IF EXISTS artifacts")
