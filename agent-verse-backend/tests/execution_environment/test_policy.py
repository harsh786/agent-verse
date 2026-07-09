"""Tests for execution-environment policy evaluation."""
from __future__ import annotations

import pytest

from app.execution_environment.envelope import build_envelope
from app.execution_environment.models import ExecutionEnvironmentPolicy
from app.execution_environment.policy import PolicyDecision, evaluate_policy, _MAX_MEMORY_MB, _MAX_WALL_CLOCK_SECONDS


def test_policy_allows_well_formed_envelope() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test goal")
    decision = evaluate_policy(env)
    assert decision.allowed is True
    assert decision.failure_reason is None


def test_policy_denies_privileged_mode() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.policy.allow_privileged = True
    decision = evaluate_policy(env)
    assert decision.allowed is False
    assert "privileged" in decision.message.lower()


def test_policy_denies_host_path_mounts() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.policy.allow_host_path_mounts = True
    decision = evaluate_policy(env)
    assert decision.allowed is False
    assert "host path" in decision.message.lower()


def test_policy_denies_empty_tenant_id() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.tenant_id = ""
    decision = evaluate_policy(env)
    assert decision.allowed is False
    assert "tenant_id" in decision.message.lower()


def test_policy_denies_empty_goal_id() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.goal_id = ""
    decision = evaluate_policy(env)
    assert decision.allowed is False
    assert "goal_id" in decision.message.lower()


def test_policy_denies_empty_goal_text() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.goal_text = ""
    decision = evaluate_policy(env)
    assert decision.allowed is False
    assert "goal_text" in decision.message.lower()


def test_policy_denies_zero_wall_clock_seconds() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.policy.resource_limits.wall_clock_seconds = 0
    decision = evaluate_policy(env)
    assert decision.allowed is False
    assert "wall_clock_seconds" in decision.message.lower()


def test_policy_denies_negative_wall_clock_seconds() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.policy.resource_limits.wall_clock_seconds = -1
    decision = evaluate_policy(env)
    assert decision.allowed is False


def test_policy_denies_zero_memory_mb() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.policy.resource_limits.memory_mb = 0
    decision = evaluate_policy(env)
    assert decision.allowed is False
    assert "memory_mb" in decision.message.lower()


def test_policy_denies_wall_clock_above_maximum() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.policy.resource_limits.wall_clock_seconds = _MAX_WALL_CLOCK_SECONDS + 1
    decision = evaluate_policy(env)
    assert decision.allowed is False
    assert "maximum" in decision.message.lower()


def test_policy_denies_memory_above_maximum() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.policy.resource_limits.memory_mb = _MAX_MEMORY_MB + 1
    decision = evaluate_policy(env)
    assert decision.allowed is False
    assert "maximum" in decision.message.lower()
