from pathlib import Path


def test_memory_learning_migration_is_linear_reversible_vector_pinned_and_rls_protected() -> None:
    source = Path("app/db/migrations/versions/0104_memory_learning.py").read_text()
    # Alembic uses the short revision ID in the variable; the full slug appears in the docstring
    assert 'revision = "0104"' in source or 'Revision ID: 0104' in source
    assert 'down_revision = "0103"' in source or 'Revises: 0103' in source
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
