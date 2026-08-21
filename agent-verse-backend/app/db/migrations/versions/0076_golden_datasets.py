"""Add golden_datasets and experiment_registry tables.

Revision ID: 0076
Revises: 0075
Create Date: 2026-07-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Golden datasets
    op.create_table(
        "golden_datasets",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("domain", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("split", sa.String(32), nullable=False, server_default="regression"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("item_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_golden_datasets_tenant", "golden_datasets", ["tenant_id"])

    # Golden dataset items
    op.create_table(
        "golden_dataset_items",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "dataset_id",
            sa.String(32),
            sa.ForeignKey("golden_datasets.id", ondelete="CASCADE"),
        ),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("goal", sa.Text, nullable=False),
        sa.Column("expected_output", sa.Text, nullable=True),
        sa.Column("human_label", sa.Boolean, nullable=True),
        sa.Column("metadata", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_golden_items_dataset", "golden_dataset_items", ["dataset_id"])

    # Experiment registry
    op.create_table(
        "experiment_registry",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False),
        sa.Column("agent_id", sa.String(32), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="proposed"),
        sa.Column("experiment_type", sa.String(64), nullable=False),
        sa.Column("config", postgresql.JSON, nullable=False, server_default="{}"),
        sa.Column("control_metrics", postgresql.JSON, nullable=True),
        sa.Column("treatment_metrics", postgresql.JSON, nullable=True),
        sa.Column("n_control", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_treatment", sa.Integer, nullable=False, server_default="0"),
        sa.Column("verdict", sa.String(32), nullable=True),  # promoted | rolled_back | inconclusive
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_experiment_registry_tenant", "experiment_registry", ["tenant_id"])

    # Add RLS to all three tables
    for table in ["golden_datasets", "golden_dataset_items", "experiment_registry"]:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.tenant_id', true))
        """)


def downgrade() -> None:
    for table in ["experiment_registry", "golden_dataset_items", "golden_datasets"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.drop_table(table)
