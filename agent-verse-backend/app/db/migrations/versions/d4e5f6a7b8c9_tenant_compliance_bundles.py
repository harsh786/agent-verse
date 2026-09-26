"""tenant_compliance_bundles — persist which compliance bundles a tenant enabled.

``ComplianceBundleManager`` was a module-level singleton over a plain dict with
no persistence at all. Enabling HIPAA/SOC2/PCI through
``POST /trust-governance/compliance-bundles/{id}/enable`` therefore wrote into
the heap of whichever API replica happened to serve the request: invisible to
every other replica, invisible to every Celery worker (which is where agents
actually execute and where an autonomy ceiling has to bind), and gone on the
next restart. The endpoint reported a compliance posture the platform did not
hold.

Small table, one row per enabled bundle, RLS-scoped like everything else.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
"""

from __future__ import annotations

from alembic import op

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_compliance_bundles (
            tenant_id   TEXT NOT NULL,
            bundle_id   TEXT NOT NULL,
            enabled_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            enabled_by  TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (tenant_id, bundle_id)
        )
        """
    )
    op.execute("ALTER TABLE tenant_compliance_bundles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_compliance_bundles FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS tenant_compliance_bundles_isolation "
        "ON tenant_compliance_bundles"
    )
    op.execute(
        "CREATE POLICY tenant_compliance_bundles_isolation ON tenant_compliance_bundles "
        "USING (tenant_id = current_setting('app.tenant_id', TRUE))"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_compliance_bundles_isolation "
        "ON tenant_compliance_bundles"
    )
    op.execute("DROP TABLE IF EXISTS tenant_compliance_bundles")
