"""Tests for medium and low severity fixes."""
from __future__ import annotations

import pytest


def test_beat_schedule_uses_correct_task_name():
    """Celery beat must use the actual registered task name for stuck-goal detection."""
    import inspect
    from app.scaling import celery_app as ca
    src = inspect.getsource(ca)
    # Should reference the real task name
    assert "app.scaling.tasks.detect_stuck_goals" in src or \
           "agentverse.maintenance.detect_stuck_goals" not in src, \
        "Beat schedule must use app.scaling.tasks.detect_stuck_goals"


def test_no_duplicate_stuck_goal_schedule():
    """Beat schedule must not have two entries for stuck-goal detection."""
    from app.scaling.celery_app import celery_app
    schedule = celery_app.conf.beat_schedule
    stuck_goal_entries = [
        k for k, v in schedule.items()
        if "stuck" in k.lower() or "detect_stuck" in v.get("task", "")
    ]
    assert len(stuck_goal_entries) == 1, (
        f"Expected exactly one stuck-goal beat schedule entry, found {stuck_goal_entries}"
    )


def test_goal_record_has_error_message_field():
    """GoalRecord dataclass must have error_message field."""
    from app.services.goal_service import GoalRecord
    r = GoalRecord(
        goal_id="g1", goal_text="t", status="failed",
        tenant_id="t1", priority="normal", dry_run=False, created_at=""
    )
    assert hasattr(r, "error_message"), "GoalRecord must have error_message field"
    r.error_message = "test error"
    assert r.error_message == "test error"


def test_document_parser_uses_get_running_loop():
    """document_parser.py must not use deprecated get_event_loop()."""
    import inspect
    from app.tools import document_parser
    src = inspect.getsource(document_parser)
    assert "get_event_loop()" not in src, \
        "document_parser.py must use get_running_loop() not get_event_loop()"


def test_shell_tool_uses_get_running_loop():
    """shell_tool.py must not use deprecated get_event_loop()."""
    import inspect
    from app.tools import shell_tool
    src = inspect.getsource(shell_tool)
    assert "get_event_loop()" not in src, \
        "shell_tool.py must use get_running_loop() not get_event_loop()"


def test_shell_tool_validates_working_dir():
    """shell_tool.py must validate working_dir to prevent path traversal."""
    import inspect
    from app.tools import shell_tool
    src = inspect.getsource(shell_tool)
    assert "_validate_working_dir" in src or "SAFE_ROOTS" in src, \
        "shell_tool.py must validate working_dir"


def test_shell_tool_validate_working_dir_safe_paths():
    """_validate_working_dir must accept safe paths and reject unsafe ones."""
    from app.tools.shell_tool import _validate_working_dir

    # Safe roots should pass through
    assert _validate_working_dir("/tmp") == "/tmp"
    assert _validate_working_dir("/tmp/subdir") == "/tmp/subdir"
    assert _validate_working_dir("/workspace") == "/workspace"
    assert _validate_working_dir("/workspace/project") == "/workspace/project"
    assert _validate_working_dir("/sandbox") == "/sandbox"

    # Unsafe paths should fall back to /tmp
    assert _validate_working_dir("/etc") == "/tmp"
    assert _validate_working_dir("/home/user") == "/tmp"
    assert _validate_working_dir("/root") == "/tmp"
    assert _validate_working_dir("") == "/tmp"

    # Path traversal attempts should fall back to /tmp
    assert _validate_working_dir("/tmp/../etc") == "/tmp"


def test_frontend_api_client_getEventLog_uses_correct_endpoint():
    """getEventLog must call /goals/{id}/events or /goals/{id}/replay, not /goals/{id}."""
    with open("/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-frontend/src/lib/api/client.ts") as f:
        src = f.read()
    assert (
        "events-log" in src
        or "/events`" in src
        or "goals/${id}/events" in src.replace("events-log", "")
        or "/replay`" in src
        or "goals/${id}/replay" in src
    ), "getEventLog must use /goals/{id}/events or /goals/{id}/replay endpoint"


def test_mock_server_goal_auto_completes():
    """Mock server must auto-complete goals for SDK testing."""
    import inspect
    import sys
    sys.path.insert(0, "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-sdk-python")
    from agentverse import mock_server
    src = inspect.getsource(mock_server)
    assert "_auto_complete" in src or "auto_complete" in src, \
        "Mock server must auto-advance goal status to complete"


def test_migration_0034_filename_correct():
    """Migration file for RLS fix must be named 0034_*.py."""
    import os
    files = os.listdir(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend"
        "/app/db/migrations/versions"
    )
    has_0034 = any("0034" in f for f in files)
    assert has_0034, "0034 migration file must exist (for RLS fix)"

    # The old misnamed file should not exist
    has_wrong_name = "0033_fix_snapshot_rls.py" in files
    assert not has_wrong_name, (
        "0033_fix_snapshot_rls.py should have been renamed to 0034_fix_snapshot_rls.py"
    )


def test_frontend_workflow_mode_is_single_agent():
    """GoalsListPage must submit workflow_mode='single_agent', not 'auto_route'."""
    with open(
        "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-frontend"
        "/src/features/goals/GoalsListPage.tsx"
    ) as f:
        src = f.read()
    assert "auto_route" not in src, (
        "GoalsListPage must not submit workflow_mode='auto_route'"
    )
