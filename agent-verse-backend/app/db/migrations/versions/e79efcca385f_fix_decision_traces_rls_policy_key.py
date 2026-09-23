"""fix decision_traces RLS policy key: app.current_tenant_id -> app.tenant_id

migration 0027 (decision_traces) created its tenant-isolation policy against
``current_setting('app.current_tenant_id', TRUE)``, but every application
code path that sets tenant scope for RLS (``app.db.rls.sqlalchemy_rls_context``
/ ``rls_context``) sets ``app.tenant_id`` instead -- the convention already
used by the other RLS policies in this schema. Migration 0034 fixed the
identical mistake for ``agent_snapshots`` and 767fe9d87bfe fixed it for
``compliance_requests``; this migration closes the same gap for
``decision_traces`` (``tool_capabilities`` is tracked/fixed separately).

Impact: under any DB role that does not have BYPASSRLS (e.g. the
least-privilege app role provisioned in production and in the NOBYPASSRLS
integration-test fixtures), FORCE ROW LEVEL SECURITY means the mismatched GUC
name is never satisfied, so every read/write against ``decision_traces``
(explainability traces written by ``app.agent.graph.AgentGraph``, read by
``GET /goals/{id}/decision-traces`` in ``app/api/goals.py``) silently returns
zero rows / fails its WITH CHECK, even though the calling code already wraps
the session in ``sqlalchemy_rls_context``. Superuser/BYPASSRLS roles never
hit this (RLS does not apply to them), which is why it was invisible in a dev
setup that connects as a superuser.

Revision ID: e79efcca385f
Revises: 767fe9d87bfe
Create Date: 2026-09-23 17:34:21.466197
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e79efcca385f"
down_revision: str | None = "767fe9d87bfe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS decision_traces_tenant_isolation ON decision_traces")
    op.execute(
        """
        CREATE POLICY decision_traces_tenant_isolation ON decision_traces
            USING (tenant_id = current_setting('app.tenant_id', TRUE))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS decision_traces_tenant_isolation ON decision_traces")
    op.execute(
        """
        CREATE POLICY decision_traces_tenant_isolation ON decision_traces
            USING (tenant_id = current_setting('app.current_tenant_id', TRUE))
        """
    )
