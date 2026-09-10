"""Tests for execution-environment data models."""
from __future__ import annotations

from app.execution_environment.models import (
    AuditLevel,
    ExecutionEnvironmentPolicy,
    ExecutionEvent,
    ExecutionFailureReason,
    ExecutionResourceLimits,
    ExecutionResult,
    FilesystemPolicy,
    NetworkPolicy,
)


def test_execution_resource_limits_defaults() -> None:
    limits = ExecutionResourceLimits()
    assert limits.cpu_cores == 1.0
    assert limits.memory_mb == 512
    assert limits.wall_clock_seconds == 1800
    assert limits.max_processes == 64


def test_execution_resource_limits_to_dict() -> None:
    limits = ExecutionResourceLimits(cpu_cores=0.5, memory_mb=256)
    d = limits.to_dict()
    assert d["cpu_cores"] == 0.5
    assert d["memory_mb"] == 256


def test_execution_environment_policy_defaults_deny_all() -> None:
    policy = ExecutionEnvironmentPolicy()
    assert policy.network_policy == NetworkPolicy.DENY_ALL
    assert policy.filesystem_policy == FilesystemPolicy.READ_ONLY_ROOT
    assert policy.allow_privileged is False
    assert policy.allow_host_path_mounts is False
    assert policy.audit_level == AuditLevel.STANDARD


def test_execution_environment_policy_to_dict() -> None:
    policy = ExecutionEnvironmentPolicy()
    d = policy.to_dict()
    assert d["network_policy"] == "deny_all"
    assert d["allow_privileged"] is False


def test_execution_event_to_sse_dict_includes_type() -> None:
    event = ExecutionEvent(
        goal_id="g1",
        tenant_id="t1",
        event_type="goal_started",
        payload={"type": "goal_started", "goal": "test"},
    )
    sse = event.to_sse_dict()
    assert sse["type"] == "goal_started"


def test_execution_event_to_sse_dict_adds_isolation_metadata_when_runner_set() -> None:
    event = ExecutionEvent(
        goal_id="g1",
        tenant_id="t1",
        event_type="step_complete",
        payload={"type": "step_complete"},
        runner_type="fake",
        capsule_id="fake-abc123",
        attempt_id="att1",
    )
    sse = event.to_sse_dict()
    assert "_isolation" in sse
    assert sse["_isolation"]["runner_type"] == "fake"
    assert sse["_isolation"]["capsule_id"] == "fake-abc123"


def test_execution_event_no_isolation_metadata_when_runner_empty() -> None:
    event = ExecutionEvent(
        goal_id="g1",
        tenant_id="t1",
        event_type="goal_complete",
        payload={"type": "goal_complete"},
    )
    sse = event.to_sse_dict()
    assert "_isolation" not in sse


def test_execution_result_to_dict_contains_required_fields() -> None:
    result = ExecutionResult(
        goal_id="g1",
        tenant_id="t1",
        attempt_id="a1",
        success=True,
        status="complete",
        iterations=3,
        runner_type="fake",
    )
    d = result.to_dict()
    assert d["goal_id"] == "g1"
    assert d["success"] is True
    assert d["status"] == "complete"
    assert d["iterations"] == 3
    assert d["runner_type"] == "fake"


def test_execution_result_failure_reason_serialises() -> None:
    result = ExecutionResult(
        goal_id="g1",
        tenant_id="t1",
        attempt_id="a1",
        success=False,
        status="failed",
        failure_reason=ExecutionFailureReason.TIMEOUT,
    )
    d = result.to_dict()
    assert d["failure_reason"] == "timeout"
