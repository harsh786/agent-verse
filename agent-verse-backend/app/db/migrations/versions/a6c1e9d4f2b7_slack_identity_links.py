"""slack_identity_links: Slack user -> AgentVerse principal (TRG-36)

Slack HITL buttons decided approvals for ANY user of a bound workspace, and the
/agentverse command submitted goals for any of them: nothing tied the Slack user
to an AgentVerse identity or role. A link row binds one Slack user
(``team_id`` + ``slack_user_id``) to one AgentVerse principal (the API key the
linking user authenticated with) in one tenant:

* ``pending`` — issued by an authenticated AgentVerse user; carries a one-time
  code (SHA-256 only) and its expiry, and no Slack identity yet.
* ``active`` — the Slack user ran ``/agentverse link <code>`` from a workspace
  bound to the same tenant.

The principal's roles are NOT copied: every Slack action re-reads the API key's
live roles/scopes, so revoking or demoting the key revokes its Slack authority.

Revision ID: a6c1e9d4f2b7
Revises: cf87de8eae52
Create Date: 2026-10-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6c1e9d4f2b7"
down_revision: str | None = "cf87de8eae52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "slack_identity_links"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("principal_id", sa.String(64), nullable=False),
        sa.Column("team_id", sa.String(64), nullable=True),
        sa.Column("slack_user_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("link_code_hash", sa.String(64), nullable=True),
        sa.Column("code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'active')", name="ck_slack_identity_status"),
    )
    op.create_index("ix_slack_identity_links_tenant", _TABLE, ["tenant_id", "status"])
    # One AgentVerse principal per Slack user.
    op.execute(
        f"CREATE UNIQUE INDEX ix_slack_identity_active_user ON {_TABLE} "
        "(team_id, slack_user_id) WHERE status = 'active'"
    )
    op.execute(
        f"CREATE UNIQUE INDEX ix_slack_identity_pending_code ON {_TABLE} "
        "(link_code_hash) WHERE status = 'pending'"
    )
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {_TABLE} AS PERMISSIVE FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {_TABLE}")
