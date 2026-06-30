"""Add roles JSONB column to api_keys to persist role assignments.

Revision ID: 0070
Revises: 0069
Create Date: 2026-06-30

The in-memory TenantService assigns roles:["admin"] to the initial API key
but sync_from_db() never persisted or loaded this field.  After the lifespan
upgrades from the in-memory service to the DB-backed one, all keys defaulted
to ("operator",) — losing governance:read, costs:read, etc.

This migration:
  - Adds a nullable JSONB `roles` column to api_keys (default NULL = operator)
  - Backfills all existing keys to roles=["admin"] (single-tenant dev setup
    where every existing key is the owner's initial key)
"""
from alembic import op

revision = "0070"
down_revision = "0069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'api_keys' AND column_name = 'roles'
            ) THEN
                ALTER TABLE api_keys ADD COLUMN roles JSONB DEFAULT '[]'::jsonb;
            END IF;
        END
        $$
    """)
    # Backfill: every existing key that has no roles gets admin (initial owner key)
    op.execute("""
        UPDATE api_keys
        SET roles = '["admin"]'::jsonb
        WHERE roles IS NULL OR roles = '[]'::jsonb
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'api_keys' AND column_name = 'roles'
            ) THEN
                ALTER TABLE api_keys DROP COLUMN roles;
            END IF;
        END
        $$
    """)
