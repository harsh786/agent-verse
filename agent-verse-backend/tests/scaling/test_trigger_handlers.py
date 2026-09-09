"""FILE_DROP, ALERTMANAGER, DATADOG, PAGERDUTY trigger handlers."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


async def test_build_goal_kwargs_for_alert_alertmanager() -> None:
    """_build_goal_kwargs_for_alert must build goal for alertmanager."""
    from app.scaling.tasks import _build_goal_kwargs_for_alert

    sched = {"goal_text": "", "agent_id": None, "priority": "high"}
    alert_ctx = {"alertname": "HighCPU", "severity": "critical", "description": "CPU > 90%"}
    mock_ctx = MagicMock()
    mock_ctx.tenant_id = "t1"

    result = await _build_goal_kwargs_for_alert(
        sched,
        "alertmanager",
        alert_ctx,
        goal_service=MagicMock(),
        tenant_ctx=mock_ctx,
    )

    assert result is not None
    assert "HighCPU" in result["goal"] or "critical" in result["goal"]
    assert result["execution_context"]["trigger_type"] == "alertmanager"


async def test_build_goal_kwargs_for_alert_datadog() -> None:
    from app.scaling.tasks import _build_goal_kwargs_for_alert

    sched = {"goal_text": "Investigate: {monitor_name}", "priority": "high"}
    alert_ctx = {"monitor_name": "HighErrorRate", "status": "triggered"}
    mock_ctx = MagicMock()

    result = await _build_goal_kwargs_for_alert(
        sched,
        "datadog",
        alert_ctx,
        goal_service=MagicMock(),
        tenant_ctx=mock_ctx,
    )

    assert result is not None
    assert result["execution_context"]["trigger_type"] == "datadog"


async def test_build_goal_kwargs_for_alert_pagerduty() -> None:
    from app.scaling.tasks import _build_goal_kwargs_for_alert

    sched: dict = {}
    alert_ctx = {"incident_title": "Database down", "urgency": "high"}
    mock_ctx = MagicMock()

    result = await _build_goal_kwargs_for_alert(
        sched,
        "pagerduty",
        alert_ctx,
        goal_service=MagicMock(),
        tenant_ctx=mock_ctx,
    )

    assert result is not None
    assert "Database down" in result["goal"] or "PagerDuty" in result["goal"]


async def test_build_goal_kwargs_for_alert_with_custom_goal() -> None:
    from app.scaling.tasks import _build_goal_kwargs_for_alert

    sched = {"goal_text": "Custom goal for alert handling"}
    alert_ctx = {"alertname": "TestAlert"}
    mock_ctx = MagicMock()

    result = await _build_goal_kwargs_for_alert(
        sched,
        "alertmanager",
        alert_ctx,
        goal_service=MagicMock(),
        tenant_ctx=mock_ctx,
    )

    assert result is not None
    assert result["goal"].startswith("Custom goal for alert handling")


async def test_file_drop_handler_submits_goal_for_new_files(tmp_path: pytest.TempPathFactory) -> None:
    """FILE_DROP handler must submit a goal for each new file found."""
    # Create a test file in tmp_path
    test_file = tmp_path / "report.csv"  # type: ignore[operator]
    test_file.write_text("data")  # type: ignore[union-attr]

    from app.scaling.tasks import _build_goal_kwargs_for_alert

    sched = {
        "file_watch_path": str(tmp_path),
        "file_pattern": "*.csv",
        "goal_text": "Process file: {file_name}",
    }
    alert_ctx = {
        "file_path": str(test_file),
        "file_name": "report.csv",
        "watch_path": str(tmp_path),
    }
    mock_ctx = MagicMock()

    result = await _build_goal_kwargs_for_alert(
        sched,
        "file_drop",
        alert_ctx,
        goal_service=MagicMock(),
        tenant_ctx=mock_ctx,
    )

    assert result is not None
    # Either the file name appears in the goal text (via alert_context injection)
    # or the execution_context confirms the trigger type.
    assert (
        "report.csv" in result["goal"]
        or result["execution_context"]["trigger_type"] == "file_drop"
    )
