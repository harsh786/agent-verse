"""Extend tool_capabilities with health/schema fields and add RLS.

Revision ID: 0030
Revises: 0029
"""
from __future__ import annotations

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_NEW_COLUMNS = [
    ("input_schema",    "JSONB       NOT NULL DEFAULT '{}'"),
    ("output_schema",   "JSONB       NOT NULL DEFAULT '{}'"),
    ("risk_level",      "TEXT        NOT NULL DEFAULT 'low'"),
    ("auth_scopes",     "JSONB       NOT NULL DEFAULT '[]'"),
    ("last_discovered", "TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
    ("health_status",   "TEXT        NOT NULL DEFAULT 'unknown'"),
    ("success_rate",    "FLOAT       NOT NULL DEFAULT 0.0"),
    ("avg_latency_ms",  "FLOAT       NOT NULL DEFAULT 0.0"),
    ("call_count",      "INT         NOT NULL DEFAULT 0"),
    ("error_count",     "INT         NOT NULL DEFAULT 0"),
    ("updated_at",      "TIMESTAMPTZ NOT NULL DEFAULT NOW()"),
]


def upgrade() -> None:
    # Add new columns to the table created in migration 0020.
    for col, typedef in _NEW_COLUMNS:
        op.execute(
            f"ALTER TABLE tool_capabilities "
            f"ADD COLUMN IF NOT EXISTS {col} {typedef}"
        )

    # Unique index for upsert semantics (ON CONFLICT in discover endpoint).
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_tool_cap_unique "
        "ON tool_capabilities (tenant_id, connector_id, tool_name)"
    )

    # Row-level security — isolate rows per tenant.
    op.execute("ALTER TABLE tool_capabilities ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tool_capabilities FORCE ROW LEVEL SECURITY")
    op.execute(
        "DO $$ BEGIN "
        "  IF NOT EXISTS ("
        "    SELECT 1 FROM pg_policies "
        "    WHERE tablename='tool_capabilities' AND policyname='tool_cap_isolation'"
        "  ) THEN "
        "    CREATE POLICY tool_cap_isolation ON tool_capabilities "
        "      USING (tenant_id = current_setting('app.current_tenant_id', TRUE)); "
        "  END IF; "
        "END $$"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tool_cap_isolation ON tool_capabilities")
    op.execute("DROP INDEX IF EXISTS ix_tool_cap_unique")
    for col, _ in _NEW_COLUMNS:
        op.execute(
            f"ALTER TABLE tool_capabilities DROP COLUMN IF EXISTS {col}"
        )
