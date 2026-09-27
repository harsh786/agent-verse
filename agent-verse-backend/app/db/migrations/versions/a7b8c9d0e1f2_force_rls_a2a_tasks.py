"""FORCE row-level security on a2a_tasks.

Migration 0023 enabled RLS on ``a2a_tasks`` but never forced it, so the table
owner — the role the default ``DATABASE_URL`` connects as — bypassed the policy.
It was missed by the earlier FORCE sweep (revision a1b2c3d4e5f6) because that
sweep matched table names with ``[a-z_]+`` and ``a2a_tasks`` contains digits.

``tests/e2e_full/test_security_sweep_e2e.py`` now checks the live catalog for
every table with a ``tenant_id`` column, so a table can no longer slip past on
its spelling.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
"""

from __future__ import annotations

from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS a2a_tasks FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE IF EXISTS a2a_tasks NO FORCE ROW LEVEL SECURITY")
