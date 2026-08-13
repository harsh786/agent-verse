from pathlib import Path


def test_program10_migration_is_linear_reversible_and_rls_protected() -> None:
    source = Path("app/db/migrations/versions/0103_routing_safety_optimization.py").read_text()
    assert 'revision = "0103_routing_safety_optimization"' in source
    assert 'down_revision = "0102_camel_generative_swarm_auction"' in source
    assert "routing_decisions" in source and "routing_outcomes" in source
    assert "ENABLE ROW LEVEL SECURITY" in source and "FORCE ROW LEVEL SECURITY" in source
    assert "WITH CHECK" in source
    assert "uq_routing_outcome_attempt_evaluator" in source
    assert "def downgrade()" in source
