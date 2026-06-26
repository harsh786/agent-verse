"""Tests that verify migration files are syntactically valid and chain correctly.

These tests are pure import-and-inspect — they do not require a database connection.
"""

from __future__ import annotations

import importlib
import inspect

import pytest

MIGRATION_REVISIONS = [
    ("app.db.migrations.versions.0001_baseline", None),
    ("app.db.migrations.versions.0002_tenancy", "0001"),
    ("app.db.migrations.versions.0003_agents", "0002"),
    ("app.db.migrations.versions.0004_goals", "0003"),
    ("app.db.migrations.versions.0005_governance", "0004"),
    ("app.db.migrations.versions.0006_mcp", "0005"),
    ("app.db.migrations.versions.0007_scheduling", "0006"),
    ("app.db.migrations.versions.0008_knowledge", "0007"),
    ("app.db.migrations.versions.0009_intelligence", "0008"),
    ("app.db.migrations.versions.0010_goal_agent_binding", "0009"),
    ("app.db.migrations.versions.0011_goal_events_checkpoints", "0010"),
    ("app.db.migrations.versions.0012_collab_metadata", "0011"),
]


def test_0012_adds_collab_session_metadata() -> None:
    module = importlib.import_module("app.db.migrations.versions.0012_collab_metadata")
    source = inspect.getsource(module)

    assert '"collab_sessions"' in source
    assert '"metadata"' in source
    assert 'server_default=sa.text("\'{}\'")' in source
    assert "ux_collab_operations_session_version" in source
    assert "unique=True" in source


def test_migration_chain_is_valid() -> None:
    """Each migration references the correct down_revision."""
    for module_path, expected_down in MIGRATION_REVISIONS:
        module = importlib.import_module(module_path)
        assert hasattr(module, "revision"), f"{module_path} missing revision"
        assert hasattr(module, "upgrade"), f"{module_path} missing upgrade()"
        assert hasattr(module, "downgrade"), f"{module_path} missing downgrade()"
        if expected_down is not None:
            assert module.down_revision == expected_down, (
                f"{module_path} down_revision mismatch: "
                f"expected {expected_down!r}, got {module.down_revision!r}"
            )
    print(f"\n✓ All {len(MIGRATION_REVISIONS)} migration files are valid")


def test_migrations_importable() -> None:
    """All migration modules can be imported without errors."""
    for module_path, _ in MIGRATION_REVISIONS:
        try:
            importlib.import_module(module_path)
        except ImportError as exc:
            pytest.fail(f"Could not import {module_path}: {exc}")


def test_0002_does_not_create_api_keys_tenant_index_twice() -> None:
    """Avoid defining the same api_keys.tenant_id index implicitly and explicitly."""
    module = importlib.import_module("app.db.migrations.versions.0002_tenancy")
    source = inspect.getsource(module.upgrade)

    assert 'index=True' not in source or 'op.create_index("ix_api_keys_tenant_id"' not in source


def test_0010_adds_goal_agent_binding_metadata_defaults() -> None:
    """0010 adds workflow metadata columns with database defaults."""
    module = importlib.import_module("app.db.migrations.versions.0010_goal_agent_binding")
    source = inspect.getsource(module)

    assert '"workflow_mode"' in source
    assert 'server_default="single_agent"' in source
    assert '"execution_context"' in source
    assert 'server_default=sa.text("\'{}\'")' in source


def test_0010_does_not_recreate_existing_agent_binding() -> None:
    """agent_id and ix_goals_tenant_agent already exist from 0004_goals."""
    module = importlib.import_module("app.db.migrations.versions.0010_goal_agent_binding")
    source = inspect.getsource(module)

    assert "agent_id" not in source
    assert "ix_goals_tenant_agent" not in source


def test_0011_adds_goal_events_and_checkpoint_foundation() -> None:
    module = importlib.import_module("app.db.migrations.versions.0011_goal_events_checkpoints")
    source = inspect.getsource(module)

    assert '"goal_events"' in source
    assert '"goal_checkpoints"' in source
    assert '"sequence"' in source
    assert '"payload"' in source
    assert 'sa.Column("sequence", sa.Integer, nullable=False, server_default="0")' in source
    assert (
        'sa.Column("payload", sa.JSON, nullable=False, server_default=sa.text("\'{}\'"))'
        in source
    )
    assert '"tenant_id", "goal_id", "sequence"' in source
    assert "goal_events_tenant_isolation" in source
    assert "goal_checkpoints_tenant_isolation" in source


@pytest.mark.parametrize("module_path,expected_down", MIGRATION_REVISIONS)
def test_migration_has_required_attributes(
    module_path: str, expected_down: str | None
) -> None:
    """Each migration module exposes revision, down_revision, upgrade, downgrade."""
    module = importlib.import_module(module_path)

    assert hasattr(module, "revision"), f"Missing 'revision' in {module_path}"
    assert isinstance(module.revision, str), f"revision must be str in {module_path}"

    assert hasattr(module, "down_revision"), f"Missing 'down_revision' in {module_path}"

    assert callable(getattr(module, "upgrade", None)), (
        f"upgrade() must be callable in {module_path}"
    )
    assert callable(getattr(module, "downgrade", None)), (
        f"downgrade() must be callable in {module_path}"
    )

    if expected_down is not None:
        assert module.down_revision == expected_down, (
            f"{module_path}: expected down_revision={expected_down!r}, "
            f"got {module.down_revision!r}"
        )
    else:
        # First migration — down_revision must be None
        assert module.down_revision is None, (
            f"{module_path}: baseline migration must have down_revision=None"
        )
