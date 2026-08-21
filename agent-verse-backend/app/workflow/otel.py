"""WorkflowOTELMiddleware — injects OTEL span attributes for workflow runs.

Wraps each step execution with a child span containing:
  - workflow.run_id
  - workflow.step_id
  - workflow.step_type
  - workflow.tenant_id

No-ops silently when OTEL is not configured.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

from app.observability.logging import get_logger

if TYPE_CHECKING:
    from app.workflow.dsl import StepDefinition
    from app.workflow.state import WorkflowState

_log = get_logger(__name__)


def _get_tracer() -> Any | None:
    try:
        from opentelemetry import trace

        return trace.get_tracer("agentverse.workflow")
    except Exception:
        return None


@contextlib.contextmanager
def step_span(
    step: StepDefinition,
    state: WorkflowState,
) -> Generator[Any, None, None]:
    """Context manager that wraps a step execution in an OTEL span.

    Usage::

        with step_span(step, state) as span:
            result = await executor(step, state)
            if span:
                span.set_attribute("workflow.result_keys", list(result.keys()))
    """
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return

    try:
        with tracer.start_as_current_span(
            f"workflow.step.{step.type}",
            attributes={
                "workflow.run_id": state.get("run_id", ""),
                "workflow.step_id": step.id,
                "workflow.step_type": step.type,
                "workflow.tenant_id": state.get("tenant_id", ""),
                "workflow.workflow_id": state.get("workflow_id", ""),
            },
        ) as span:
            yield span
    except Exception as exc:
        _log.debug("otel_span_error", error=str(exc))
        yield None


@contextlib.contextmanager
def run_span(
    workflow_id: str,
    run_id: str,
    tenant_id: str,
    trigger_type: str = "manual",
) -> Generator[Any, None, None]:
    """Span wrapping an entire workflow run."""
    tracer = _get_tracer()
    if tracer is None:
        yield None
        return

    try:
        with tracer.start_as_current_span(
            "workflow.run",
            attributes={
                "workflow.run_id": run_id,
                "workflow.workflow_id": workflow_id,
                "workflow.tenant_id": tenant_id,
                "workflow.trigger_type": trigger_type,
            },
        ) as span:
            yield span
    except Exception as exc:
        _log.debug("otel_run_span_error", error=str(exc))
        yield None
