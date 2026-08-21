"""Add RLS to 6 tenant tables that were missing policies.

Revision ID: 0071
Revises: 170245f26dcb
Create Date: 2026-07-04
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0071"
down_revision: str | None = "170245f26dcb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    tables = [
        "eval_suites",
        "eval_suite_results",
        "connector_health_snapshots",
        "self_optimization_suggestions",
        "ip_allowlist",
        "user_roles",
    ]
    for table in tables:
        # Enable RLS — idempotent: re-enabling on an already-enabled table is a no-op
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # Drop existing policy if any (idempotent)
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        # Create tenant isolation policy
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
            """
        )


def downgrade() -> None:
    tables = [
        "eval_suites",
        "eval_suite_results",
        "connector_health_snapshots",
        "self_optimization_suggestions",
        "ip_allowlist",
        "user_roles",
    ]
    for table in tables:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
