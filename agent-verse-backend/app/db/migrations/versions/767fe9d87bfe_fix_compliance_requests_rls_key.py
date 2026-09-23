"""fix compliance_requests RLS policy key: app.current_tenant_id -> app.tenant_id

migration 0026 (compliance_requests) created its tenant-isolation policy
against ``current_setting('app.current_tenant_id', TRUE)``, but every
application code path that sets tenant scope for RLS
(``app.db.rls.sqlalchemy_rls_context`` / ``rls_context``) sets
``app.tenant_id`` instead -- the convention already used by ~190 other RLS
policies in this schema. Migration 0034 fixed the identical mistake for
``agent_snapshots`` but missed ``compliance_requests`` (and
``decision_traces`` / ``tool_capabilities``, tracked separately).

Impact: under any DB role that does not have BYPASSRLS (e.g. the
least-privilege app role provisioned in production and in the
NOBYPASSRLS integration-test fixtures), FORCE ROW LEVEL SECURITY means the
mismatched GUC name is never satisfied, so
``ComplianceController._db_save_request`` / ``_db_load_request`` (the
GDPR export-request persistence path in ``app/enterprise/compliance.py``)
silently fails every read/write against Postgres (caught by a broad
``except Exception`` and logged) and falls back to the in-process dict --
so a GDPR export request's ready state/payload does not survive a restart
or reach a different replica, even though the module's own docstring
promises durable Postgres persistence. Superuser/BYPASSRLS roles never hit
this (RLS does not apply to them), which is why it was invisible in a dev
setup that connects as a superuser.

Revision ID: 767fe9d87bfe
Revises: 619ba480ee3a
Create Date: 2026-09-23 17:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "767fe9d87bfe"
down_revision: str | None = "619ba480ee3a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS compliance_requests_tenant_isolation ON compliance_requests")
    op.execute(
        """
        CREATE POLICY compliance_requests_tenant_isolation ON compliance_requests
            USING (tenant_id = current_setting('app.tenant_id', TRUE))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS compliance_requests_tenant_isolation ON compliance_requests")
    op.execute(
        """
        CREATE POLICY compliance_requests_tenant_isolation ON compliance_requests
            USING (tenant_id = current_setting('app.current_tenant_id', TRUE))
        """
    )
