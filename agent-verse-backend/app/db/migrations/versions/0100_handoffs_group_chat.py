"""Extend canonical handoffs and transcript metadata.

Revision ID: 0100_handoffs_group_chat
Revises: 0099_reasoning_eval
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0100_handoffs_group_chat"
down_revision = "0099_reasoning_eval"
branch_labels = None
depends_on = None


def _json(
    name: str, *, nullable: bool = False, list_default: bool = False
) -> sa.Column[object]:
    return sa.Column(
        name,
        postgresql.JSONB(),
        nullable=nullable,
        server_default=(
            None
            if nullable
            else sa.text("'[]'::jsonb" if list_default else "'{}'::jsonb")
        ),
    )


def upgrade() -> None:
    handoff_columns = (
        sa.Column("civilization_id", sa.Text(), nullable=False, server_default="legacy"),
        _json("target_membership_snapshot"),
        _json("connector_allowlist", list_default=True),
        sa.Column("classification", sa.Text(), nullable=False, server_default="internal"),
        sa.Column("result_reference", sa.Text(), nullable=True),
        sa.Column("acceptance_token_digest", sa.Text(), nullable=False, server_default=""),
        _json("transition_audit", list_default=True),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("idempotency_key", sa.Text(), nullable=False, server_default="legacy"),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
    )
    for column in handoff_columns:
        op.add_column("handoffs", column)
    message_columns = (
        sa.Column("encrypted_content_reference", sa.Text(), nullable=True),
        sa.Column("artifact_reference", sa.Text(), nullable=True),
        sa.Column("clearance_decision", sa.Text(), nullable=False, server_default="allowed"),
        _json("taint_chain", list_default=True),
        sa.Column("source_digest", sa.Text(), nullable=False, server_default=""),
        sa.Column("compacts_from_sequence", sa.BigInteger(), nullable=True),
        sa.Column("compacts_to_sequence", sa.BigInteger(), nullable=True),
        sa.Column("trust_label", sa.Text(), nullable=False, server_default="legacy_unknown"),
    )
    for column in message_columns:
        op.add_column("context_messages", column)
    op.create_unique_constraint(
        "uq_handoffs_tenant_session_idempotency",
        "handoffs",
        ["tenant_id", "session_id", "idempotency_key"],
    )
    op.create_index(
        "ix_handoffs_tenant_session_state",
        "handoffs",
        ["tenant_id", "session_id", "state"],
    )
    op.create_index(
        "ix_context_messages_tenant_session_sequence",
        "context_messages",
        ["tenant_id", "session_id", "sequence"],
    )
    for table in ("handoffs", "context_messages"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            "USING (tenant_id = current_setting('app.tenant_id', true)) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_index("ix_context_messages_tenant_session_sequence", table_name="context_messages")
    op.drop_index("ix_handoffs_tenant_session_state", table_name="handoffs")
    op.drop_constraint("uq_handoffs_tenant_session_idempotency", "handoffs", type_="unique")
    for name in (
        "trust_label", "compacts_to_sequence", "compacts_from_sequence", "source_digest",
        "taint_chain", "clearance_decision", "artifact_reference", "encrypted_content_reference",
    ):
        op.drop_column("context_messages", name)
    for name in (
        "deadline", "idempotency_key", "schema_version", "transition_audit",
        "acceptance_token_digest", "result_reference", "classification",
        "connector_allowlist", "target_membership_snapshot", "civilization_id",
    ):
        op.drop_column("handoffs", name)
