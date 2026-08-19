"""Persist CAMEL, generative, swarm, and auction coordination state.

Revision ID: 0102_camel_generative_swarm_auction
Revises: 0101_magentic_moa
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0102"
down_revision = "0101"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        "USING (tenant_id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
    )


def _base() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(32),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )


def upgrade() -> None:
    # Program revision names now exceed Alembic's historical 32-character
    # default. Widen once, before this revision is recorded.
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(32),
        type_=sa.String(128),
        existing_nullable=False,
    )
    op.create_table(
        "camel_dialogue_state",
        *_base(),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("execution_id", sa.Text(), nullable=False),
        sa.Column("role_contracts", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("contract_digest", sa.Text(), nullable=False),
        sa.Column("phase", sa.Text(), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("termination_checks", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.UniqueConstraint("tenant_id", "execution_id", name="uq_camel_execution"),
        sa.CheckConstraint("turn_count >= 0", name="ck_camel_turn_count"),
    )
    op.create_index("ix_camel_tenant_session", "camel_dialogue_state", ["tenant_id", "session_id"])
    op.create_table(
        "generative_agent_state",
        *_base(),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("execution_id", sa.Text(), nullable=False),
        sa.Column("persona_id", sa.Text(), nullable=False),
        sa.Column("persona_version", sa.Integer(), nullable=False),
        sa.Column("simulation_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reflection_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("plan_cursor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.UniqueConstraint("tenant_id", "execution_id", name="uq_generative_execution"),
        sa.CheckConstraint("event_count >= 0", name="ck_generative_event_count"),
    )
    op.create_index(
        "ix_generative_tenant_session", "generative_agent_state", ["tenant_id", "session_id"]
    )
    op.create_table(
        "swarm_gossip_messages",
        *_base(),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("civilization_id", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("origin_agent_id", sa.Text(), nullable=False),
        sa.Column("origin_credential_digest", sa.Text(), nullable=False),
        sa.Column("message_type", sa.Text(), nullable=False),
        sa.Column("payload_digest", sa.Text(), nullable=False),
        sa.Column("hops_remaining", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.UniqueConstraint("tenant_id", "event_id", name="uq_swarm_gossip_event"),
        sa.CheckConstraint("hops_remaining >= 0", name="ck_swarm_hops"),
    )
    op.create_index(
        "ix_swarm_gossip_tenant_session_expiry",
        "swarm_gossip_messages",
        ["tenant_id", "session_id", "expires_at"],
    )
    op.create_table(
        "task_auctions",
        *_base(),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("work_item_id", sa.Text(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scoring_policy_version", sa.Text(), nullable=False),
        sa.Column("announcement", postgresql.JSONB(), nullable=False),
        sa.Column("unseal_authority", sa.Text(), nullable=False),
        sa.Column("unsealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("winner_agent_id", sa.Text(), nullable=True),
        sa.Column("winner_fencing_token", sa.Integer(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id", "work_item_id", "round_number", name="uq_task_auction_round"
        ),
    )
    op.create_index("ix_task_auctions_tenant_session", "task_auctions", ["tenant_id", "session_id"])
    for table in (
        "camel_dialogue_state",
        "generative_agent_state",
        "swarm_gossip_messages",
        "task_auctions",
    ):
        _rls(table)

    for name, type_, default in (
        ("claim_attempt", sa.Integer(), "1"),
        ("reclaim_count", sa.Integer(), "0"),
        ("convergence_digest", sa.Text(), None),
    ):
        op.add_column(
            "claims", sa.Column(name, type_, nullable=default is None, server_default=default)
        )
    for name, type_, default in (
        ("auction_id", sa.Text(), None),
        ("bid_version", sa.Integer(), "1"),
        ("governor_attestation", sa.Text(), ""),
        ("sealed_payload", sa.Text(), ""),
        ("nonce", sa.Text(), ""),
        ("signature", sa.Text(), ""),
        ("commitment", sa.Text(), ""),
        ("eligible", sa.Boolean(), "false"),
        ("invalid_reason", sa.Text(), None),
    ):
        op.add_column(
            "agent_bids", sa.Column(name, type_, nullable=default is None, server_default=default)
        )
    op.create_unique_constraint(
        "uq_agent_bids_auction_bidder_version",
        "agent_bids",
        ["tenant_id", "auction_id", "bidder_agent_id", "bid_version"],
    )
    for name, type_, default in (
        ("auction_id", sa.Text(), None),
        ("fairness_adjustment", sa.Numeric(18, 6), "0"),
        ("explanation", postgresql.JSONB(), "{}"),
        ("winner_lease_id", sa.Text(), None),
        ("winner_fencing_token", sa.Integer(), None),
        ("rebid_round", sa.Integer(), "0"),
        ("fallback_state", sa.Text(), None),
        ("settlement_state", sa.Text(), None),
        ("idempotency_key", sa.Text(), ""),
    ):
        op.add_column(
            "allocations", sa.Column(name, type_, nullable=default is None, server_default=default)
        )


def downgrade() -> None:
    for name in (
        "idempotency_key",
        "settlement_state",
        "fallback_state",
        "rebid_round",
        "winner_fencing_token",
        "winner_lease_id",
        "explanation",
        "fairness_adjustment",
        "auction_id",
    ):
        op.drop_column("allocations", name)
    op.drop_constraint("uq_agent_bids_auction_bidder_version", "agent_bids", type_="unique")
    for name in (
        "invalid_reason",
        "eligible",
        "commitment",
        "signature",
        "nonce",
        "sealed_payload",
        "governor_attestation",
        "bid_version",
        "auction_id",
    ):
        op.drop_column("agent_bids", name)
    for name in ("convergence_digest", "reclaim_count", "claim_attempt"):
        op.drop_column("claims", name)
    for table in (
        "task_auctions",
        "swarm_gossip_messages",
        "generative_agent_state",
        "camel_dialogue_state",
    ):
        op.drop_table(table)
