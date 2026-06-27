"""Add missing agent fields: system_prompt, model_override, max_iterations,
timeout_seconds, allowed_collection_ids, eval_suite_id, policy_ids, version, is_archived."""

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    # Add columns that are missing from the ORM model and table
    for col_def in [
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS system_prompt TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS model_override TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_iterations INT NOT NULL DEFAULT 15",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS timeout_seconds INT NOT NULL DEFAULT 300",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS allowed_collection_ids JSONB NOT NULL DEFAULT '[]'",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS eval_suite_id TEXT",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS policy_ids JSONB NOT NULL DEFAULT '[]'",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS version INT NOT NULL DEFAULT 1",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS cloned_from TEXT",
    ]:
        op.execute(col_def)
    # Add FORCE ROW LEVEL SECURITY
    op.execute("ALTER TABLE agents FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    from alembic import op

    for col in [
        "system_prompt",
        "model_override",
        "max_iterations",
        "timeout_seconds",
        "allowed_collection_ids",
        "eval_suite_id",
        "policy_ids",
        "version",
        "is_archived",
        "cloned_from",
    ]:
        op.execute(f"ALTER TABLE agents DROP COLUMN IF EXISTS {col}")
