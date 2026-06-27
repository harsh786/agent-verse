"""Add template_versions table for marketplace versioning."""
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS template_versions (
            id            TEXT PRIMARY KEY,
            template_id   TEXT NOT NULL,
            version       TEXT NOT NULL,
            changelog     TEXT NOT NULL DEFAULT '',
            template_data JSONB NOT NULL DEFAULT '{}',
            published_by  TEXT NOT NULL DEFAULT '',
            published_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_template_ver "
        "ON template_versions (template_id, published_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_template_ver")
    op.execute("DROP TABLE IF EXISTS template_versions")
