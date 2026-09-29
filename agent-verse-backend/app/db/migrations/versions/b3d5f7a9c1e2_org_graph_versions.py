"""org_graph_versions: persist U8 knowledge-graph version history per tenant.

``GET /v1/org/{org_id}/graph/versions`` read a process-global in-memory
VersionStore keyed by ``knowledge_graph:<org_id>`` alone — no tenant filter and
no org-ownership check — so any tenant could read (and append to) another
tenant's org graph history by id, and the history differed per replica and
vanished on restart. Written/read by ``OrgService.save_graph_version`` /
``list_graph_versions``.

Tenant-isolated by FORCE'd RLS using ``app_current_tenant_uuid()``
(``c9d0e1f2a3b4``).

Revision ID: b3d5f7a9c1e2
Revises: a9c4e2f7b1d3
"""

from __future__ import annotations

from alembic import op

revision = "b3d5f7a9c1e2"
down_revision = "a9c4e2f7b1d3"
branch_labels = None
depends_on = None

_TABLE = "org_graph_versions"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            id            UUID PRIMARY KEY,
            tenant_id     UUID NOT NULL,
            org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
            version_num   INTEGER NOT NULL,
            content_hash  VARCHAR(64) NOT NULL,
            snapshot      JSONB NOT NULL DEFAULT '{{}}',
            changed_by    VARCHAR(200) NOT NULL DEFAULT 'system',
            change_reason TEXT NOT NULL DEFAULT '',
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_org_graph_versions_tenant_org_num
                UNIQUE (tenant_id, org_id, version_num)
        )
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_isolation ON {_TABLE}")
    op.execute(
        f"CREATE POLICY {_TABLE}_isolation ON {_TABLE} "
        "USING (tenant_id = app_current_tenant_uuid()) "
        "WITH CHECK (tenant_id = app_current_tenant_uuid())"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_TABLE} CASCADE")
