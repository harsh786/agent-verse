from pathlib import Path

from app.db.models.coordination import COORDINATION_TABLES


def test_magentic_moa_migration_is_linear_reversible_and_rls_protected() -> None:
    source = Path("app/db/migrations/versions/0101_magentic_moa.py").read_text()
    # Internal alembic revision IDs are short-numeric across the whole chain
    # (repo-wide convention; the descriptive name lives only in the filename).
    assert 'revision = "0101"' in source
    assert 'down_revision = "0100"' in source
    for table in ("moa_layers", "moa_proposals"):
        assert table in source and table in COORDINATION_TABLES
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "WITH CHECK" in source
    assert "uq_progress_ledger_tenant_session_version" in source
    assert "uq_moa_proposals_attempt" in source
    assert "def downgrade()" in source
