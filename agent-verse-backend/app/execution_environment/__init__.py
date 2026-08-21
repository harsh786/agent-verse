"""Isolated Agent Execution Environment.

This package implements a containment and blast-radius-reduction layer for
agent workloads.  It does NOT change agent behaviour — the same AgentGraph /
AgentGraph code runs inside the isolated environment.

Architecture
------------
Control plane (backend API / Celery):
  - Accepts goals, enforces governance/guardrails/HITL, schedules execution.
  - Serialises an :class:`ExecutionEnvelope` and hands it to the
    :class:`ExecutionEnvironmentScheduler`.

Execution plane (isolated runner):
  - Receives the envelope, reconstructs only the services it needs,
    runs the existing agent workload, and streams :class:`ExecutionEvent`s
    back to the control plane.

Feature flags (all default False — existing behaviour is preserved):
  ``ISOLATED_AGENT_EXECUTION``          — master switch
  ``ISOLATED_EXECUTION_REQUIRED``       — fail-closed when runner unavailable
  ``ISOLATED_EXECUTION_LOCAL_RUNNER``   — enable subprocess runner
  ``ISOLATED_EXECUTION_KUBERNETES_RUNNER`` — enable Kubernetes Job runner
"""

from __future__ import annotations

from app.execution_environment.models import (
    ExecutionArtifact,
    ExecutionEnvelope,
    ExecutionEnvironmentPolicy,
    ExecutionEnvironmentSpec,
    ExecutionEvent,
    ExecutionFailureReason,
    ExecutionRequest,
    ExecutionResourceLimits,
    ExecutionResult,
)
from app.execution_environment.scheduler import ExecutionEnvironmentScheduler

__all__ = [
    "ExecutionArtifact",
    "ExecutionEnvelope",
    "ExecutionEnvironmentPolicy",
    "ExecutionEnvironmentScheduler",
    "ExecutionEnvironmentSpec",
    "ExecutionEvent",
    "ExecutionFailureReason",
    "ExecutionRequest",
    "ExecutionResourceLimits",
    "ExecutionResult",
]
