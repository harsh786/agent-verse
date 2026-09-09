"""Tests for HMAC field coverage — verifying that security-relevant envelope
fields are included in the canonical bytes and cannot be tampered in transit.

G-48: Proves that policy fields (network_policy, resource_limits, egress_allowlist,
spec.runner_type) invalidate the signature when tampered.
"""
from __future__ import annotations

import pytest

from app.execution_environment.envelope import build_envelope, verify_envelope
from app.execution_environment.models import (
    CodeExecutionWorkload,
    CodeWorkloadMode,
    ExecutionKind,
    NetworkPolicy,
    RunnerType,
)


def test_hmac_covers_network_policy() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    original_sig = env.signature

    # Tamper network policy after signing
    env.policy.network_policy = NetworkPolicy.ALLOW_TENANT_ALLOWLIST

    assert not verify_envelope(env), (
        "network_policy change should invalidate HMAC — "
        "a tampered envelope could escalate network access"
    )


def test_hmac_covers_resource_limits_memory() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")

    # Tamper memory limit after signing
    env.policy.resource_limits.memory_mb = 999_999

    assert not verify_envelope(env), (
        "memory_mb change should invalidate HMAC — "
        "a tampered envelope could bypass memory limits"
    )


def test_hmac_covers_resource_limits_wall_clock() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.policy.resource_limits.wall_clock_seconds = 999_999
    assert not verify_envelope(env)


def test_hmac_covers_egress_allowlist() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.policy.egress_allowlist = ["0.0.0.0/0"]  # tamper to allow all egress
    assert not verify_envelope(env), (
        "egress_allowlist change should invalidate HMAC — "
        "a tampered envelope could open unrestricted network access"
    )


def test_hmac_covers_allow_privileged() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.policy.allow_privileged = True  # attempt privilege escalation
    assert not verify_envelope(env), (
        "allow_privileged change should invalidate HMAC"
    )


def test_hmac_covers_spec_runner_type() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test", runner_type=RunnerType.FAKE)
    env.spec.runner_type = RunnerType.LOCAL  # attempt runner substitution
    assert not verify_envelope(env), (
        "runner_type change should invalidate HMAC"
    )


def test_hmac_covers_feature_flags() -> None:
    env = build_envelope(
        tenant_id="t1", goal_id="g1", goal_text="test",
        feature_flags={"isolated_agent_execution": True},
    )
    env.feature_flags["isolated_execution_required"] = True
    assert not verify_envelope(env)


def test_hmac_covers_goal_text() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="original")
    env.goal_text = "INJECTED_PAYLOAD"
    assert not verify_envelope(env)


def test_hmac_covers_tenant_id() -> None:
    env = build_envelope(tenant_id="tenant-a", goal_id="g1", goal_text="test")
    env.tenant_id = "tenant-b"  # attempt cross-tenant execution
    assert not verify_envelope(env)


def test_hmac_valid_after_round_trip() -> None:
    """A signed envelope that is NOT tampered must always verify successfully."""
    env = build_envelope(
        tenant_id="t1",
        goal_id="g1",
        goal_text="legitimate goal",
        feature_flags={"isolated_agent_execution": True},
    )
    assert verify_envelope(env), "Unmodified signed envelope must verify"


def test_hmac_expired_envelope_fails_verification() -> None:
    """An envelope older than max_age_seconds must fail verification."""
    from datetime import UTC, datetime, timedelta

    from app.execution_environment.envelope import sign_envelope
    from app.execution_environment.models import ExecutionEnvelope

    old_envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="old goal")
    # Manually set issued_at to 2 hours ago
    old_envelope.issued_at = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    # Re-sign with the new (old) issued_at
    sign_envelope(old_envelope)

    # Should fail with default 1-hour TTL
    assert not verify_envelope(old_envelope, max_age_seconds=3600)


def test_hmac_skipping_expiry_check() -> None:
    """With max_age_seconds=0 the expiry check is skipped."""
    from datetime import UTC, datetime, timedelta

    from app.execution_environment.envelope import sign_envelope

    old_envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="old goal")
    old_envelope.issued_at = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    sign_envelope(old_envelope)

    assert verify_envelope(old_envelope, max_age_seconds=0), (
        "With max_age_seconds=0 the expiry check should be skipped"
    )


def test_hmac_covers_every_code_workload_field() -> None:
    workload = CodeExecutionWorkload.create(
        workload_id="workload",
        mode=CodeWorkloadMode.PROGRAM_OF_THOUGHT,
        source="result = 4",
        stdin_json={"input": 2},
        expected_output_schema={"type": "integer"},
        requested_artifacts=("result.json",),
    )
    envelope = build_envelope(
        tenant_id="tenant",
        goal_id="goal",
        execution_kind=ExecutionKind.CODE_INTERPRETER,
        code_workload=workload,
    )
    assert verify_envelope(envelope)
    envelope.code_workload = workload.model_copy(update={"stdin_json": {"input": 3}})
    assert not verify_envelope(envelope)
