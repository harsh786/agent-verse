"""Add citation metadata and freshness tracking to knowledge documents."""
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    stmts = [
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_url TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'text'",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_doc_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS page_number INT",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS last_modified TIMESTAMPTZ",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS freshness_ttl_hours INT NOT NULL DEFAULT 168",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS needs_reindex BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ",
        "CREATE INDEX IF NOT EXISTS ix_documents_needs_reindex ON documents (needs_reindex) WHERE needs_reindex = TRUE",
        "CREATE INDEX IF NOT EXISTS ix_documents_source_type ON documents (collection_id, source_type)",
        "CREATE INDEX IF NOT EXISTS ix_documents_source_doc_id ON documents (source_doc_id) WHERE source_doc_id != ''",
    ]
    for stmt in stmts:
        op.execute(stmt)


def downgrade() -> None:
    for col in ["source_url", "source_type", "source_doc_id", "page_number",
                "last_modified", "freshness_ttl_hours", "needs_reindex", "updated_at"]:
        op.execute(f"ALTER TABLE documents DROP COLUMN IF EXISTS {col}")
    op.execute("DROP INDEX IF EXISTS ix_documents_needs_reindex")
    op.execute("DROP INDEX IF EXISTS ix_documents_source_type")
    op.execute("DROP INDEX IF EXISTS ix_documents_source_doc_id")
