"""fix tool_capabilities RLS policy key: app.current_tenant_id -> app.tenant_id

migration 0030 (tool_capabilities) created its tenant-isolation policy
(``tool_cap_isolation``) against ``current_setting('app.current_tenant_id',
TRUE)``, but every application code path that sets tenant scope for RLS
(``app.db.rls.sqlalchemy_rls_context`` / ``rls_context``) sets
``app.tenant_id`` instead -- the same mistake already fixed for
``agent_snapshots`` (0034), ``compliance_requests`` (767fe9d87bfe) and
``decision_traces`` (e79efcca385f).

Impact: under any DB role that does not have BYPASSRLS (e.g. the
least-privilege app role provisioned in production and in the NOBYPASSRLS
integration-test fixtures), FORCE ROW LEVEL SECURITY means the mismatched GUC
name is never satisfied, so reads/writes against ``tool_capabilities`` (the
MCP tool discovery/health registry, e.g. ``GET /connectors/capabilities`` in
``app/api/connectors.py``, which already wraps its session in
``sqlalchemy_rls_context``) silently return zero rows / fail their WITH
CHECK. Superuser/BYPASSRLS roles never hit this, which is why it was
invisible in a dev setup that connects as a superuser.

Note: two other tool_capabilities write paths (``discover_connector_tools``
in ``app/api/connectors.py`` and ``MCPClient._update_tool_stats`` in
``app/mcp/client.py``) execute raw INSERT/UPDATE statements without ever
wrapping the session in ``sqlalchemy_rls_context``/``rls_context`` at all --
that is a separate application-code bug (the GUC is never set on those
paths, so this policy fix alone does not make them see/affect rows under a
non-BYPASSRLS role) tracked separately from this migration.

Revision ID: 06f8d39de7a2
Revises: e79efcca385f
Create Date: 2026-09-23 17:34:39.491844
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "06f8d39de7a2"
down_revision: str | None = "e79efcca385f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tool_cap_isolation ON tool_capabilities")
    op.execute(
        """
        CREATE POLICY tool_cap_isolation ON tool_capabilities
            USING (tenant_id = current_setting('app.tenant_id', TRUE))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', TRUE))
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tool_cap_isolation ON tool_capabilities")
    op.execute(
        """
        CREATE POLICY tool_cap_isolation ON tool_capabilities
            USING (tenant_id = current_setting('app.current_tenant_id', TRUE))
        """
    )
