"""Regression: retention / partition maintenance tasks could never fail.

Per-table and per-partition errors were folded into ``"error: ..."`` strings
(or a top-level ``{"error": ...}``) and the dict was returned, which Celery
records as SUCCESS — a retention job that never deleted anything looked healthy.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.scaling import tasks


def test_retention_with_a_failed_table_fails_the_task() -> None:
    result = {"retention_days": 90, "deleted": {"goal_events": 12, "audit": "error: denied"}}
    with (
        patch("app.scaling.tasks._delete_expired_records", AsyncMock(return_value=result)),
        pytest.raises(RuntimeError, match="audit"),
    ):
        tasks.execute_retention_policy.run()


def test_clean_retention_run_succeeds() -> None:
    result = {"retention_days": 90, "deleted": {"goal_events": 3}}
    with patch("app.scaling.tasks._delete_expired_records", AsyncMock(return_value=result)):
        assert tasks.execute_retention_policy.run() == result


def test_partition_errors_fail_the_task() -> None:
    result = {"created": {}, "errors": {"cost_ledger_2026_10": "must be owner"}}
    with (
        patch("app.scaling.tasks._ensure_future_partitions", AsyncMock(return_value=result)),
        pytest.raises(RuntimeError, match="must be owner"),
    ):
        tasks.create_guardrail_partitions.run()


def test_top_level_error_fails_the_task() -> None:
    with pytest.raises(RuntimeError, match="no db"):
        tasks._fail_task_on_errors("x", {"error": "no db"})
