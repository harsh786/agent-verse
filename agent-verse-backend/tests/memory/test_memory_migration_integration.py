from pathlib import Path


def test_memory_learning_migration_is_linear_reversible_vector_pinned_and_rls_protected() -> None:
    source = Path("app/db/migrations/versions/0104_memory_learning.py").read_text()
    assert 'revision = "0104_memory_learning"' in source
    assert 'down_revision = "0103_routing_safety_optimization"' in source
    for table in (
        "memory_records",
        "memory_feedback",
        "prospective_memories",
        "learning_experiments",
        "learning_experiment_outcomes",
    ):
        assert table in source
    assert "Vector(1536)" in source
    assert "ENABLE ROW LEVEL SECURITY" in source and "FORCE ROW LEVEL SECURITY" in source
    assert "WITH CHECK" in source and "def downgrade()" in source
