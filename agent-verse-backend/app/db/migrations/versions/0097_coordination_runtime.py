"""Add the canonical durable coordination runtime.

Revision ID: 0097_coordination_runtime
Revises: 0096_strategy_runtime_v2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0097"
down_revision = "0096"
branch_labels = None
depends_on = None

COORDINATION_TABLES: dict[str, tuple[str, ...]] = {
    "coordination_sessions": (
        "civilization_id",
        "goal_id",
        "state",
        "policy_snapshot",
        "budget_snapshot",
        "deadline",
        "cancellation_requested_at",
        "cancellation_reason",
        "next_sequence",
    ),
    "strategy_executions": (
        "session_id",
        "goal_id",
        "adapter_id",
        "adapter_version",
        "state_schema_version",
        "profile_snapshot",
        "state",
        "result",
        "cost",
        "idempotency_key",
        "deadline",
        "attempt",
        "prior_execution_id",
    ),
    "context_messages": (
        "session_id",
        "sequence",
        "sender_agent_id",
        "recipient_agent_ids",
        "message_type",
        "content",
        "provenance",
        "classification",
        "idempotency_key",
        "expires_at",
    ),
    "progress_ledger_revisions": ("session_id", "objective", "state"),
    "work_items": ("session_id", "state", "dependencies"),
    "handoffs": ("session_id", "source_agent_id", "target_agent_id", "state"),
    "claims": (
        "work_item_id",
        "owner_agent_id",
        "lease_id",
        "fencing_token",
        "heartbeat_at",
        "lease_expires_at",
        "state",
    ),
    "agent_bids": ("work_item_id", "bidder_agent_id", "sealed", "score"),
    "allocations": ("session_id", "work_item_id", "state"),
    "thought_nodes": ("session_id", "execution_id", "safe_summary", "depth"),
    "thought_edges": ("session_id", "execution_id", "source_node_id", "target_node_id"),
    "strategy_artifacts": (
        "session_id",
        "execution_id",
        "artifact_type",
        "content_reference",
    ),
    "strategy_checkpoints": ("session_id", "execution_id", "sequence", "state_reference"),
    "event_inbox": ("event_id", "consumer_name", "state", "fencing_token"),
    "approval_grants": ("session_id", "action_digest", "nonce_digest", "state"),
    "budget_accounts": ("session_id", "ceiling", "reserved", "committed"),
    "budget_reservations": ("session_id", "account_id", "amount", "state"),
    "budget_entries": ("session_id", "account_id", "entry_type", "amount"),
    "coordination_events": (
        "session_id",
        "sequence",
        "schema_version",
        "event_type",
        "occurred_at",
        "correlation_id",
        "causation_id",
        "idempotency_key",
        "classification",
        "expires_at",
        "payload",
    ),
    "coordination_outbox": (
        "event_id",
        "session_id",
        "stream",
        "payload",
        "state",
        "attempt_count",
        "available_at",
        "claim_owner",
        "claimed_at",
        "published_at",
        "last_error",
    ),
    "coordination_dead_letters": ("event_id", "session_id", "payload", "replay_status"),
    "coordination_consumptions": ("event_id", "consumer_name", "outcome", "consumed_at"),
}

LEGACY_RLS_TABLES = (
    "civilizations",
    "civilization_agents",
    "spawn_requests",
    "blackboard_entries",
    "bus_messages",
    "civilization_learnings",
    "civilization_events",
    "goal_events",
    "goal_checkpoints",
)

INTEGER_COLUMNS = frozenset(
    {
        "next_sequence",
        "sequence",
        "schema_version",
        "state_schema_version",
        "fencing_token",
        "depth",
        "attempt",
        "attempt_count",
    }
)
NULLABLE_COLUMNS = frozenset(
    {
        "cancellation_reason",
        "prior_execution_id",
    }
)
NUMERIC_COLUMNS = frozenset({"score", "cost", "ceiling", "reserved", "committed", "amount"})
BOOLEAN_COLUMNS = frozenset({"sealed"})
JSON_COLUMNS = frozenset(
    {
        "policy_snapshot",
        "budget_snapshot",
        "profile_snapshot",
        "result",
        "content",
        "recipient_agent_ids",
        "provenance",
        "dependencies",
        "payload",
    }
)
DATETIME_COLUMNS = frozenset(
    {
        "deadline",
        "cancellation_requested_at",
        "expires_at",
        "occurred_at",
        "available_at",
        "claimed_at",
        "published_at",
        "consumed_at",
        "heartbeat_at",
        "lease_expires_at",
    }
)


def _domain_column(name: str) -> sa.Column[object]:
    nullable = name in NULLABLE_COLUMNS
    if name in INTEGER_COLUMNS:
        return sa.Column(name, sa.BigInteger(), nullable=nullable, server_default="0")
    if name in NUMERIC_COLUMNS:
        return sa.Column(name, sa.Numeric(18, 6), nullable=False, server_default="0")
    if name in BOOLEAN_COLUMNS:
        return sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())
    if name in JSON_COLUMNS:
        return sa.Column(
            name,
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        )
    if name in DATETIME_COLUMNS:
        return sa.Column(name, sa.DateTime(timezone=True), nullable=True)
    return sa.Column(name, sa.Text(), nullable=nullable)


def _rls_policy(table_name: str) -> None:
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table_name}_tenant_isolation ON {table_name} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def upgrade() -> None:
    for table_name, domain_columns in COORDINATION_TABLES.items():
        constraints: list[sa.SchemaItem] = []
        if "sequence" in domain_columns and "session_id" in domain_columns:
            constraints.append(
                sa.UniqueConstraint(
                    "tenant_id",
                    "session_id",
                    "sequence",
                    name=f"uq_{table_name}_tenant_session_sequence",
                )
            )
        if table_name == "coordination_outbox":
            constraints.append(sa.UniqueConstraint("event_id", name="uq_coordination_outbox_event"))
        if table_name == "coordination_consumptions":
            constraints.append(
                sa.UniqueConstraint(
                    "tenant_id",
                    "event_id",
                    "consumer_name",
                    name="uq_coordination_consumption",
                )
            )
        op.create_table(
            table_name,
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column(
                "tenant_id",
                sa.String(32),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
            ),
            *(_domain_column(column_name) for column_name in domain_columns),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            *constraints,
        )
        op.create_index(f"ix_{table_name}_tenant", table_name, ["tenant_id"])
        _rls_policy(table_name)

    for table_name in LEGACY_RLS_TABLES:
        policy_name = f"{table_name}_tenant_isolation"
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}")
        _rls_policy(table_name)


def downgrade() -> None:
    for table_name in reversed(tuple(COORDINATION_TABLES)):
        op.execute(f"DROP POLICY IF EXISTS {table_name}_tenant_isolation ON {table_name}")
        op.drop_index(f"ix_{table_name}_tenant", table_name=table_name)
        op.drop_table(table_name)

    for table_name in LEGACY_RLS_TABLES:
        policy_name = f"{table_name}_tenant_isolation"
        op.execute(f"DROP POLICY IF EXISTS {policy_name} ON {table_name}")
        op.execute(
            f"CREATE POLICY {policy_name} ON {table_name} "
            "USING (tenant_id = current_setting('app.tenant_id', true))"
        )
