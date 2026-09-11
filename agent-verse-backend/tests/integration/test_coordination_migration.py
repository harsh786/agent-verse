from __future__ import annotations

from pathlib import Path

import pytest

from app.db.models import Base

pytestmark = pytest.mark.integration

COORDINATION_TABLES = {
    "coordination_sessions",
    "strategy_executions",
    "context_messages",
    "progress_ledger_revisions",
    "work_items",
    "handoffs",
    "claims",
    "agent_bids",
    "allocations",
    "thought_nodes",
    "thought_edges",
    "strategy_artifacts",
    "strategy_checkpoints",
    "event_inbox",
    "approval_grants",
    "budget_accounts",
    "budget_reservations",
    "budget_entries",
    "coordination_events",
    "coordination_outbox",
    "coordination_dead_letters",
    "coordination_consumptions",
}

REQUIRED_COLUMNS = {
    "coordination_sessions": {"civilization_id", "goal_id", "state", "next_sequence"},
    "strategy_executions": {"session_id", "goal_id", "adapter_id", "state"},
    "context_messages": {"session_id", "sequence", "sender_agent_id", "content"},
    "progress_ledger_revisions": {"session_id", "objective", "state"},
    "work_items": {"session_id", "state", "dependencies"},
    "handoffs": {"session_id", "source_agent_id", "target_agent_id", "state"},
    "claims": {
        "work_item_id", "owner_agent_id", "lease_id", "fencing_token",
        "heartbeat_at", "lease_expires_at", "state",
    },
    "agent_bids": {"work_item_id", "bidder_agent_id", "sealed", "score"},
    "allocations": {"session_id", "work_item_id", "state"},
    "thought_nodes": {"session_id", "execution_id", "safe_summary", "depth"},
    "thought_edges": {"session_id", "execution_id", "source_node_id", "target_node_id"},
    "strategy_artifacts": {"session_id", "execution_id", "artifact_type", "content_reference"},
    "strategy_checkpoints": {"session_id", "execution_id", "sequence", "state_reference"},
    "event_inbox": {"event_id", "consumer_name", "state", "fencing_token"},
    "approval_grants": {"session_id", "action_digest", "nonce_digest", "state"},
    "budget_accounts": {"session_id", "ceiling", "reserved", "committed"},
    "budget_reservations": {"session_id", "account_id", "amount", "state"},
    "budget_entries": {"session_id", "account_id", "entry_type", "amount"},
    "coordination_events": {"session_id", "sequence", "event_type", "payload"},
    "coordination_outbox": {"event_id", "session_id", "stream", "state", "attempt_count"},
    "coordination_dead_letters": {"event_id", "session_id", "payload", "replay_status"},
    "coordination_consumptions": {"event_id", "consumer_name", "outcome"},
}


def test_coordination_models_register_every_canonical_table() -> None:
    import app.db.models.coordination  # noqa: F401

    assert set(Base.metadata.tables) >= COORDINATION_TABLES
    for table_name in COORDINATION_TABLES:
        columns = Base.metadata.tables[table_name].columns
        assert "tenant_id" in columns
        assert "id" in columns
        assert REQUIRED_COLUMNS[table_name] <= set(columns.keys())


def test_coordination_migration_is_linear_and_reversible() -> None:
    migration = Path("app/db/migrations/versions/0097_coordination_runtime.py")
    source = migration.read_text()

    # Revision ids are the numeric form; the descriptive name lives in the
    # filename and docstring only.
    assert 'revision = "0097"' in source
    assert 'down_revision = "0096"' in source
    assert "def downgrade()" in source
    for table_name in COORDINATION_TABLES:
        assert table_name in source
