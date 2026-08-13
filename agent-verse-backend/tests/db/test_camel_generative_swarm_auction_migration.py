from pathlib import Path

from app.db.models.coordination import COORDINATION_TABLES


def test_program09_migration_is_linear_reversible_and_forces_rls() -> None:
    source = Path("app/db/migrations/versions/0102_camel_generative_swarm_auction.py").read_text()
    assert 'revision = "0102_camel_generative_swarm_auction"' in source
    assert 'down_revision = "0101_magentic_moa"' in source
    for table in (
        "camel_dialogue_state",
        "generative_agent_state",
        "swarm_gossip_messages",
        "task_auctions",
    ):
        assert table in source and table in COORDINATION_TABLES
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "WITH CHECK" in source
    assert "uq_agent_bids_auction_bidder_version" in source
    assert "winner_fencing_token" in source
    assert "def downgrade()" in source
