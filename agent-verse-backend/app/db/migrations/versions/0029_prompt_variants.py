"""Create prompt_variants table for persistent A/B prompt optimization state."""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS prompt_variants (
            id             TEXT        PRIMARY KEY,
            tenant_id      TEXT        NOT NULL DEFAULT 'global',
            prompt_key     TEXT        NOT NULL,
            variant_name   TEXT        NOT NULL,
            prompt_text    TEXT        NOT NULL DEFAULT '',
            is_control     BOOLEAN     NOT NULL DEFAULT FALSE,
            win_count      INT         NOT NULL DEFAULT 0,
            loss_count     INT         NOT NULL DEFAULT 0,
            is_active      BOOLEAN     NOT NULL DEFAULT TRUE,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_prompt_variants_tenant_key "
        "ON prompt_variants (tenant_id, prompt_key)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_prompt_variants_unique "
        "ON prompt_variants (tenant_id, prompt_key, variant_name)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_prompt_variants_unique")
    op.execute("DROP INDEX IF EXISTS ix_prompt_variants_tenant_key")
    op.execute("DROP TABLE IF EXISTS prompt_variants")
