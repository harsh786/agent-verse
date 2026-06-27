"""Tests for P3 phase implementations."""
from __future__ import annotations

import os
import pytest


def test_migration_0043_exists():
    files = os.listdir(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend"
        "/app/db/migrations/versions"
    )
    assert any("0043" in f for f in files), "Migration 0043 for debate audit must exist"


def test_file_drop_trigger_type():
    from app.triggers.models import TriggerType

    assert hasattr(TriggerType, "FILE_DROP") or "file_drop" in [
        t.value for t in TriggerType
    ], "TriggerType must have FILE_DROP"


def test_alertmanager_trigger_type():
    from app.triggers.models import TriggerType

    assert "alertmanager" in [t.value for t in TriggerType], \
        "TriggerType must have ALERTMANAGER"


def test_datadog_trigger_type():
    from app.triggers.models import TriggerType

    assert "datadog" in [t.value for t in TriggerType], \
        "TriggerType must have DATADOG"


def test_alertmanager_endpoint_exists():
    from app.main import create_app

    app = create_app()
    schema = app.openapi()
    paths = list(schema.get("paths", {}).keys())
    assert any("alertmanager" in p for p in paths), \
        "/events/alertmanager endpoint must exist"


def test_datadog_endpoint_exists():
    from app.main import create_app

    app = create_app()
    schema = app.openapi()
    paths = list(schema.get("paths", {}).keys())
    assert any("datadog" in p for p in paths), \
        "/events/datadog endpoint must exist"


def test_tool_inverses_has_github():
    import inspect

    from app.reliability import tool_inverses

    src = inspect.getsource(tool_inverses)
    assert "github" in src.lower() or "create_issue" in src, \
        "tool_inverses must have GitHub inverse implementations"


def test_github_inverse_registered():
    from app.reliability.tool_inverses import _INVERSE_REGISTRY

    assert "github:create_issue" in _INVERSE_REGISTRY or \
        "github_create_issue" in _INVERSE_REGISTRY, \
        "github_create_issue must be registered in the inverse registry"


def test_debate_persist_method_exists():
    from app.collab.agent_collab import AgentCollabSession

    session = AgentCollabSession(goal="test")
    assert hasattr(session, "persist_debate"), \
        "AgentCollabSession must have persist_debate method"


def test_simulation_page_exists():
    path = (
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse"
        "/agent-verse-frontend/src/features/simulation/SimulationPage.tsx"
    )
    assert os.path.exists(path), "SimulationPage.tsx must exist"


def test_rpa_live_page_exists():
    path = (
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse"
        "/agent-verse-frontend/src/features/rpa/RpaLivePage.tsx"
    )
    assert os.path.exists(path), "RpaLivePage.tsx must exist"


def test_audit_explorer_page_exists():
    path = (
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse"
        "/agent-verse-frontend/src/features/audit/AuditExplorerPage.tsx"
    )
    assert os.path.exists(path), "AuditExplorerPage.tsx must exist"


def test_app_tsx_has_simulation_route():
    path = (
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse"
        "/agent-verse-frontend/src/app/App.tsx"
    )
    content = open(path).read()
    assert "simulation" in content, "App.tsx must have /simulation route"
    assert "audit" in content, "App.tsx must have /audit route"
    assert "rpa/live" in content, "App.tsx must have /rpa/live route"
