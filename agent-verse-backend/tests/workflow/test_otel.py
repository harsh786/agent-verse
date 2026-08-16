"""Tests for WorkflowOTEL span helpers (graceful no-op when OTEL not installed)."""
from __future__ import annotations

import pytest

from app.workflow.dsl import StepDefinition
from app.workflow.otel import run_span, step_span
from app.workflow.state import WorkflowRunStatus


def _make_state() -> dict:
    return {
        "run_id": "run-abc",
        "workflow_id": "wf-1",
        "tenant_id": "tenant-1",
        "status": WorkflowRunStatus.RUNNING,
    }


def _make_step() -> StepDefinition:
    return StepDefinition(id="step1", type="tool", tool="my.tool")


def test_step_span_no_op_when_no_tracer() -> None:
    """step_span should not raise even without OTEL configured."""
    step = _make_step()
    state = _make_state()  # type: ignore[arg-type]
    with step_span(step, state) as span:  # type: ignore[arg-type]
        # span may be None (no tracer) or a real span — both are acceptable
        assert span is None or hasattr(span, "set_attribute")


def test_run_span_no_op_when_no_tracer() -> None:
    """run_span should not raise even without OTEL configured."""
    with run_span("wf-1", "run-abc", "tenant-1", "manual") as span:
        assert span is None or hasattr(span, "set_attribute")


def test_step_span_returns_context_manager() -> None:
    """step_span must be usable as a context manager."""
    step = _make_step()
    state = _make_state()  # type: ignore[arg-type]
    # Should not raise
    with step_span(step, state) as span:  # type: ignore[arg-type]
        pass  # no-op


def test_run_span_returns_context_manager() -> None:
    """run_span must be usable as a context manager."""
    with run_span("wf-x", "run-x", "ten-x") as span:
        pass


def test_step_span_nesting() -> None:
    """Nested spans should not raise."""
    step = _make_step()
    state = _make_state()  # type: ignore[arg-type]
    with step_span(step, state) as outer:  # type: ignore[arg-type]
        with step_span(step, state) as inner:  # type: ignore[arg-type]
            pass
