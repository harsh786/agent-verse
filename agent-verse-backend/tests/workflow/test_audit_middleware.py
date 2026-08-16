"""Tests for AutoAuditMiddleware — automatic audit event emission."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.workflow.audit_middleware import AutoAuditMiddleware
from app.workflow.state import WorkflowRunStatus


@pytest.fixture
def audit_log() -> MagicMock:
    return MagicMock()


@pytest.fixture
def middleware(audit_log: MagicMock) -> AutoAuditMiddleware:
    return AutoAuditMiddleware(audit_log=audit_log)


def _state(**kwargs) -> dict:
    defaults = {
        "run_id": "run-1",
        "workflow_id": "wf-1",
        "tenant_id": "t-1",
        "status": WorkflowRunStatus.RUNNING,
        "inputs": {},
    }
    defaults.update(kwargs)
    return defaults


def test_run_started_emits_audit(middleware: AutoAuditMiddleware, audit_log: MagicMock) -> None:
    middleware.run_started(_state())  # type: ignore[arg-type]
    audit_log.record.assert_called_once()


def test_step_started_emits_audit(middleware: AutoAuditMiddleware, audit_log: MagicMock) -> None:
    middleware.step_started(_state(), step_id="s1", step_type="tool")  # type: ignore[arg-type]
    audit_log.record.assert_called_once()


def test_step_completed_emits_audit(middleware: AutoAuditMiddleware, audit_log: MagicMock) -> None:
    middleware.step_completed(
        _state(), step_id="s1", output={"result": "ok"},  # type: ignore[arg-type]
        duration_ms=123, cost_usd=0.001
    )
    audit_log.record.assert_called_once()


def test_step_failed_emits_audit(middleware: AutoAuditMiddleware, audit_log: MagicMock) -> None:
    middleware.step_failed(_state(), step_id="s1", error="Timeout")  # type: ignore[arg-type]
    audit_log.record.assert_called_once()


def test_step_skipped_emits_audit(middleware: AutoAuditMiddleware, audit_log: MagicMock) -> None:
    middleware.step_skipped(_state(), step_id="s1", reason="depends_on not met")  # type: ignore[arg-type]
    audit_log.record.assert_called_once()


def test_hitl_requested_emits_audit(middleware: AutoAuditMiddleware, audit_log: MagicMock) -> None:
    middleware.hitl_requested(
        _state(), step_id="review", request_id="req-1",  # type: ignore[arg-type]
        assignee_role="compliance", deadline_at="2026-12-01T00:00:00Z"
    )
    audit_log.record.assert_called_once()


def test_hitl_decided_emits_audit(middleware: AutoAuditMiddleware, audit_log: MagicMock) -> None:
    middleware.hitl_decided(
        _state(), step_id="review",  # type: ignore[arg-type]
        action="approved", reviewer_id="user-abc"
    )
    audit_log.record.assert_called_once()


def test_run_completed_emits_audit(middleware: AutoAuditMiddleware, audit_log: MagicMock) -> None:
    middleware.run_completed(_state(), outputs_hash="abc123", total_cost_usd=0.001, duration_ms=100)  # type: ignore[arg-type]
    audit_log.record.assert_called_once()


def test_run_failed_emits_audit(middleware: AutoAuditMiddleware, audit_log: MagicMock) -> None:
    middleware.run_failed(_state(), error_code="step_failed", failed_step_id="s1")  # type: ignore[arg-type]
    audit_log.record.assert_called_once()


def test_run_paused_emits_audit(middleware: AutoAuditMiddleware, audit_log: MagicMock) -> None:
    middleware.run_paused(_state(), paused_by="admin", reason="operator")  # type: ignore[arg-type]
    audit_log.record.assert_called_once()


def test_run_resumed_emits_audit(middleware: AutoAuditMiddleware, audit_log: MagicMock) -> None:
    middleware.run_resumed(_state(), resumed_by="admin")  # type: ignore[arg-type]
    audit_log.record.assert_called_once()


def test_no_error_without_audit_log() -> None:
    m = AutoAuditMiddleware(audit_log=None)
    m.run_started(_state())  # type: ignore[arg-type]  # Should not raise


