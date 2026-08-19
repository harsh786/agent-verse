"""Add knowledge_nodes and knowledge_edges tables.

Revision ID: 0085_add_knowledge_graph
Revises: 0084_add_tenant_mfa
Create Date: 2026-07-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0085"
down_revision: str | None = "0084"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_nodes",
        sa.Column("id", sa.String(255), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("node_type", sa.String(50), nullable=False),
        sa.Column("label", sa.String(500), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("source_id", sa.String(255), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kn_tenant_id", "knowledge_nodes", ["tenant_id"])
    op.create_index("ix_kn_node_type", "knowledge_nodes", ["node_type"])
    op.create_index("ix_kn_tenant_type", "knowledge_nodes", ["tenant_id", "node_type"])
    op.create_index("ix_kn_tenant_label", "knowledge_nodes", ["tenant_id", "label"])

    op.create_table(
        "knowledge_edges",
        sa.Column("id", sa.String(255), primary_key=True, nullable=False),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("source_node_id", sa.String(255), nullable=False),
        sa.Column("target_node_id", sa.String(255), nullable=False),
        sa.Column("edge_type", sa.String(50), nullable=False),
        sa.Column("label", sa.String(255), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("provenance", sa.String(255), nullable=True),
        sa.Column("extra_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ke_tenant", "knowledge_edges", ["tenant_id"])
    op.create_index("ix_ke_source", "knowledge_edges", ["source_node_id"])
    op.create_index("ix_ke_target", "knowledge_edges", ["target_node_id"])


def downgrade() -> None:
    op.drop_index("ix_ke_target", table_name="knowledge_edges")
    op.drop_index("ix_ke_source", table_name="knowledge_edges")
    op.drop_index("ix_ke_tenant", table_name="knowledge_edges")
    op.drop_table("knowledge_edges")

    op.drop_index("ix_kn_tenant_label", table_name="knowledge_nodes")
    op.drop_index("ix_kn_tenant_type", table_name="knowledge_nodes")
    op.drop_index("ix_kn_node_type", table_name="knowledge_nodes")
    op.drop_index("ix_kn_tenant_id", table_name="knowledge_nodes")
    op.drop_table("knowledge_nodes")
