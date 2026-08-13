from __future__ import annotations

from pathlib import Path

import pytest

from app.db.models.goal import Goal

pytestmark = pytest.mark.integration


def test_goal_model_has_versioned_runtime_profile_snapshot_columns() -> None:
    columns = Goal.__table__.columns
    assert "runtime_profile_version" in columns
    assert "strategy_registry_revision" in columns
    assert "runtime_profile_snapshot" in columns
    assert "rejected_strategies" in columns


def test_strategy_runtime_migration_is_linear_reversible_and_tenant_scoped() -> None:
    migration = Path("app/db/migrations/versions/0096_strategy_runtime_v2.py").read_text()

    assert 'revision = "0096_strategy_runtime_v2"' in migration
    assert 'down_revision = "0095_raft_lifecycle"' in migration
    assert "strategy_certification_evidence" in migration
    assert "runtime_profile_version" in migration
    assert "strategy_registry_revision" in migration
    assert "runtime_profile_snapshot" in migration
    assert "rejected_strategies" in migration
    assert "ENABLE ROW LEVEL SECURITY" in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "USING (tenant_id = current_setting('app.tenant_id', true))" in migration
    assert "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))" in migration
    assert "def downgrade()" in migration
