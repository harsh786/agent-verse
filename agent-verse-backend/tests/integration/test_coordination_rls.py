from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.test_coordination_migration import COORDINATION_TABLES

pytestmark = pytest.mark.integration


def test_every_coordination_table_enables_forces_and_checks_rls() -> None:
    source = Path(
        "app/db/migrations/versions/0097_coordination_runtime.py"
    ).read_text()

    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "WITH CHECK" in source
    assert "current_setting('app.tenant_id', true)" in source
    assert "for table_name, domain_columns in COORDINATION_TABLES.items()" in source
    assert "_rls_policy(table_name)" in source


def test_migration_repairs_legacy_policies_without_editing_old_revisions() -> None:
    source = Path(
        "app/db/migrations/versions/0097_coordination_runtime.py"
    ).read_text()

    assert "LEGACY_RLS_TABLES" in source
    assert "goal_events" in source
    assert "goal_checkpoints" in source
    assert "WITH CHECK" in source
    assert len(COORDINATION_TABLES) == 22
