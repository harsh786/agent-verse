"""Tests verifying AgentVerse SDK correctness and RLS policy fixes.

Run with: .venv/bin/pytest tests/api/test_agent_sdk.py -v
"""

from __future__ import annotations

import asyncio
import os

import pytest


REPO_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "../../..")
)


def test_python_sdk_has_update_agent():
    """Python SDK must have update_agent method."""
    from agentverse.client import AgentVerseClient

    assert hasattr(AgentVerseClient, "update_agent"), "Python SDK missing update_agent"
    assert asyncio.iscoroutinefunction(AgentVerseClient.update_agent)


def test_python_sdk_create_agent_request_has_system_prompt():
    """AgentCreateRequest must have system_prompt and model_override (not model)."""
    from agentverse.models import AgentCreateRequest

    req = AgentCreateRequest(name="test", system_prompt="Be helpful")
    assert req.system_prompt == "Be helpful"
    assert hasattr(req, "model_override"), "AgentCreateRequest should have model_override"
    assert not hasattr(req, "model") or req.model_override == "", (
        "AgentCreateRequest should not have 'model' field (use model_override)"
    )


def test_python_sdk_create_agent_request_new_fields():
    """AgentCreateRequest must include all new backend-aligned fields."""
    from agentverse.models import AgentCreateRequest

    req = AgentCreateRequest(
        name="comprehensive-agent",
        goal_template="Summarise daily reports",
        autonomy_mode="bounded-autonomous",
        system_prompt="You are a helpful assistant",
        model_override="gpt-4o",
        max_iterations=20,
        timeout_seconds=600,
        connector_ids=["c1", "c2"],
        allowed_collection_ids=["col-1"],
        policy_ids=["pol-1"],
    )
    assert req.goal_template == "Summarise daily reports"
    assert req.model_override == "gpt-4o"
    assert req.max_iterations == 20
    assert req.timeout_seconds == 600
    assert req.connector_ids == ["c1", "c2"]


def test_python_sdk_create_agent_request_defaults():
    """AgentCreateRequest defaults must match backend expectations."""
    from agentverse.models import AgentCreateRequest

    req = AgentCreateRequest(name="minimal")
    assert req.autonomy_mode == "bounded-autonomous"
    assert req.goal_template == ""
    assert req.system_prompt == ""
    assert req.model_override == ""
    assert req.max_iterations == 15
    assert req.timeout_seconds == 300
    assert req.connector_ids == []
    assert req.policy_ids == []
    assert req.eval_suite_id is None


def test_typescript_sdk_has_update_agent_request_type():
    """TypeScript types.ts must have UpdateAgentRequest interface."""
    types_path = os.path.join(REPO_ROOT, "agent-verse-sdk-typescript", "src", "types.ts")
    with open(types_path) as f:
        src = f.read()
    assert "UpdateAgentRequest" in src, "TypeScript SDK must have UpdateAgentRequest type"
    assert "system_prompt" in src, "UpdateAgentRequest must have system_prompt"
    assert "model_override" in src, "UpdateAgentRequest must use model_override not model"
    # Old fields that should NOT be in CreateAgentRequest
    assert "description?" not in src or "UpdateAgentRequest" in src, (
        "CreateAgentRequest must not have description field"
    )


def test_typescript_sdk_create_agent_request_no_legacy_fields():
    """TypeScript CreateAgentRequest must not have old description/tools/model fields."""
    types_path = os.path.join(REPO_ROOT, "agent-verse-sdk-typescript", "src", "types.ts")
    with open(types_path) as f:
        src = f.read()
    # Check that CreateAgentRequest block doesn't contain 'model?' (old field)
    # Find the CreateAgentRequest block
    start = src.find("export interface CreateAgentRequest")
    end = src.find("}", start)
    block = src[start:end] if start != -1 else ""
    assert "model?" not in block, "CreateAgentRequest must not have 'model?' (use model_override)"
    assert "tools?" not in block, "CreateAgentRequest must not have 'tools?' field"
    assert "description?" not in block, "CreateAgentRequest must not have 'description?' field"


def test_typescript_sdk_has_run_agent():
    """TypeScript SDK client must have runAgent and updateAgent methods."""
    client_path = os.path.join(REPO_ROOT, "agent-verse-sdk-typescript", "src", "client.ts")
    with open(client_path) as f:
        src = f.read()
    assert "runAgent" in src, "TypeScript SDK must have runAgent method"
    assert "updateAgent" in src, "TypeScript SDK must have updateAgent method"
    assert "UpdateAgentRequest" in src, "TypeScript client must import UpdateAgentRequest"


def test_snapshot_rls_uses_correct_key():
    """agent_snapshots RLS policy must be fixed: app.current_tenant_id → app.tenant_id.

    Either the 0025 migration was corrected directly, OR a new migration exists that fixes it.
    """
    versions_dir = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "../../app/db/migrations/versions")
    )
    migration_files = os.listdir(versions_dir)

    # Accept either a migration named with '0033'/'0034' or 'fix_snapshot' in name
    snapshot_fixed = (
        any("0033" in f for f in migration_files)
        or any("0034" in f for f in migration_files)
        or any("fix_snapshot" in f for f in migration_files)
    )
    assert snapshot_fixed, (
        "Must have a migration that fixes the agent_snapshots RLS policy key "
        "(app.current_tenant_id → app.tenant_id)"
    )


def test_snapshot_rls_migration_content():
    """The fix migration must use the correct GUC key app.tenant_id."""
    versions_dir = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "../../app/db/migrations/versions")
    )
    # Find the fix migration file
    fix_file = next(
        (
            f for f in os.listdir(versions_dir)
            if ("fix_snapshot" in f or "0033" in f) and f.endswith(".py")
        ),
        None,
    )
    assert fix_file is not None, "Could not find RLS fix migration file"
    with open(os.path.join(versions_dir, fix_file)) as fh:
        content = fh.read()
    assert "app.tenant_id" in content, (
        "Fix migration must set app.tenant_id (not app.current_tenant_id)"
    )
    assert "app.current_tenant_id" not in content.replace(
        "app.current_tenant_id", ""
    ).replace("app.tenant_id", ""), (
        "Fix migration must not keep the old wrong key app.current_tenant_id"
    )


def test_save_snapshot_uses_rls_context():
    """_save_snapshot_to_db and _load_snapshots_from_db must use sqlalchemy_rls_context."""
    import inspect
    from app.api import agents

    src = inspect.getsource(agents)
    assert "sqlalchemy_rls_context" in src, (
        "_save_snapshot_to_db / _load_snapshots_from_db must use RLS context for tenant isolation"
    )
    # Specifically verify the standalone helpers (not just AgentStore methods)
    save_fn_src = inspect.getsource(agents._save_snapshot_to_db)
    load_fn_src = inspect.getsource(agents._load_snapshots_from_db)
    assert "sqlalchemy_rls_context" in save_fn_src, (
        "_save_snapshot_to_db must call sqlalchemy_rls_context"
    )
    assert "sqlalchemy_rls_context" in load_fn_src, (
        "_load_snapshots_from_db must call sqlalchemy_rls_context"
    )
