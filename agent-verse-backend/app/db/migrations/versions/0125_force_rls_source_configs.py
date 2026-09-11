"""force_rls_source_configs — FORCE row-level security on source_configs.

source_configs (ingestion Sources, item 6) had RLS ENABLED with a correct
per-tenant isolation policy, but not FORCE'd. Without FORCE, the table OWNER
bypasses RLS — so under a production non-superuser owner role a caller could
read another tenant's Sources. The other tenant tables (workflows, goals,
api_keys, …) already FORCE RLS; this brings source_configs in line.

(No effect under the dev superuser role, which bypasses RLS regardless; this
hardens the production posture.)

Revision ID: 0125
Revises: 0124
"""

from __future__ import annotations

from alembic import op

revision = "0125"
down_revision = "0124"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE source_configs FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute("ALTER TABLE source_configs NO FORCE ROW LEVEL SECURITY")
