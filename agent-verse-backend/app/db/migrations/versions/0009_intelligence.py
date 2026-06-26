"""Create decision_traces, evaluations, cost_ledger, collab_sessions,
collab_operations, and agent_templates tables.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── decision_traces ───────────────────────────────────────────────────
    op.create_table(
        "decision_traces",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "goal_id",
            sa.String(32),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("step_id", sa.String(32), nullable=True, server_default=""),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("reasoning", sa.Text, nullable=True, server_default=""),
        sa.Column("evidence", sa.JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "alternatives", sa.JSON, nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "confidence", sa.Float, nullable=True, server_default=sa.text("0.0")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_decision_traces_goal_id", "decision_traces", ["goal_id"])
    op.create_index("ix_decision_traces_tenant_id", "decision_traces", ["tenant_id"])

    op.execute("ALTER TABLE decision_traces ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE decision_traces FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY decision_traces_tenant_isolation ON decision_traces "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ── evaluations ───────────────────────────────────────────────────────
    op.create_table(
        "evaluations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "goal_id",
            sa.String(32),
            sa.ForeignKey("goals.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("scores", sa.JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "average_score", sa.Float, nullable=False, server_default=sa.text("0.0")
        ),
        sa.Column(
            "passed", sa.Boolean, nullable=False, server_default=sa.text("FALSE")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_evaluations_goal_id", "evaluations", ["goal_id"])
    op.create_index("ix_evaluations_tenant_id", "evaluations", ["tenant_id"])

    op.execute("ALTER TABLE evaluations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE evaluations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY evaluations_tenant_isolation ON evaluations "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ── cost_ledger ───────────────────────────────────────────────────────
    op.create_table(
        "cost_ledger",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("goal_id", sa.String(32), nullable=True, server_default=""),
        sa.Column("tool_name", sa.String(200), nullable=True, server_default=""),
        sa.Column("cost_usd", sa.Float, nullable=False),
        sa.Column(
            "tokens_used", sa.Integer, nullable=True, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_cost_ledger_tenant_id", "cost_ledger", ["tenant_id"])
    op.create_index("ix_cost_ledger_goal_id", "cost_ledger", ["goal_id"])

    op.execute("ALTER TABLE cost_ledger ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE cost_ledger FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY cost_ledger_tenant_isolation ON cost_ledger "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ── collab_sessions ───────────────────────────────────────────────────
    op.create_table(
        "collab_sessions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("mode", sa.String(50), nullable=True, server_default="suggest"),
        sa.Column("status", sa.String(20), nullable=True, server_default="active"),
        sa.Column("content", sa.Text, nullable=True, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_collab_sessions_tenant_id", "collab_sessions", ["tenant_id"])

    op.execute("ALTER TABLE collab_sessions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE collab_sessions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY collab_sessions_tenant_isolation ON collab_sessions "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ── collab_operations (CRDT-style OT log) ────────────────────────────
    op.create_table(
        "collab_operations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(32),
            sa.ForeignKey("collab_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column(
            "version", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "operation", JSONB, nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("author", sa.String(200), nullable=True, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_collab_operations_session_id", "collab_operations", ["session_id"]
    )
    op.create_index(
        "ix_collab_operations_tenant_id", "collab_operations", ["tenant_id"]
    )

    op.execute("ALTER TABLE collab_operations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE collab_operations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY collab_operations_tenant_isolation ON collab_operations "
        "USING (tenant_id = current_setting('app.tenant_id', true))"
    )

    # ── agent_templates (tenant_id NULL = system-level template) ─────────
    op.create_table(
        "agent_templates",
        sa.Column("id", sa.String(32), primary_key=True),
        # NULL means a system/public template not owned by any tenant
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("domain", sa.String(100), nullable=True, server_default=""),
        sa.Column("description", sa.Text, nullable=True, server_default=""),
        sa.Column("goal_template", sa.Text, nullable=False),
        sa.Column(
            "connectors", sa.JSON, nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column(
            "trigger_type", sa.String(20), nullable=True, server_default="rest"
        ),
        sa.Column(
            "autonomy_mode",
            sa.String(50),
            nullable=True,
            server_default="bounded-autonomous",
        ),
        sa.Column(
            "is_public", sa.Boolean, nullable=True, server_default=sa.text("TRUE")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_agent_templates_tenant_id", "agent_templates", ["tenant_id"])

    op.execute("ALTER TABLE agent_templates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_templates FORCE ROW LEVEL SECURITY")
    # System templates (tenant_id IS NULL) are visible to every tenant
    op.execute(
        "CREATE POLICY agent_templates_tenant_isolation ON agent_templates "
        "USING (tenant_id IS NULL OR tenant_id = current_setting('app.tenant_id', true))"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS agent_templates_tenant_isolation ON agent_templates"
    )
    op.drop_table("agent_templates")

    op.execute(
        "DROP POLICY IF EXISTS collab_operations_tenant_isolation ON collab_operations"
    )
    op.drop_table("collab_operations")

    op.execute(
        "DROP POLICY IF EXISTS collab_sessions_tenant_isolation ON collab_sessions"
    )
    op.drop_table("collab_sessions")

    op.execute("DROP POLICY IF EXISTS cost_ledger_tenant_isolation ON cost_ledger")
    op.drop_table("cost_ledger")

    op.execute(
        "DROP POLICY IF EXISTS evaluations_tenant_isolation ON evaluations"
    )
    op.drop_table("evaluations")

    op.execute(
        "DROP POLICY IF EXISTS decision_traces_tenant_isolation ON decision_traces"
    )
    op.drop_table("decision_traces")
