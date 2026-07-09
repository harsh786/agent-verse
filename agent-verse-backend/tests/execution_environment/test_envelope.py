"""Tests for ExecutionEnvelope contracts and HMAC signing."""
from __future__ import annotations

import os

import pytest

from app.execution_environment.envelope import (
    build_envelope,
    sign_envelope,
    verify_envelope,
)
from app.execution_environment.models import (
    ExecutionEnvelope,
    ExecutionEnvironmentPolicy,
    RunnerType,
)


def test_build_envelope_sets_required_fields() -> None:
    env = build_envelope(
        tenant_id="t1",
        goal_id="g1",
        goal_text="Do something",
    )
    assert env.tenant_id == "t1"
    assert env.goal_id == "g1"
    assert env.goal_text == "Do something"
    assert env.attempt_id != ""
    assert env.correlation_id != ""
    assert env.issued_at != ""


def test_build_envelope_is_signed() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    assert env.signature != ""
    assert verify_envelope(env) is True


def test_verify_envelope_detects_tampering() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.goal_text = "TAMPERED"  # mutate after signing
    assert verify_envelope(env) is False


def test_verify_envelope_fails_empty_signature() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    env.signature = ""
    assert verify_envelope(env) is False


def test_build_envelope_default_policy_is_deny_all() -> None:
    from app.execution_environment.models import NetworkPolicy
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    assert env.policy.network_policy == NetworkPolicy.DENY_ALL
    assert env.policy.allow_privileged is False
    assert env.policy.allow_host_path_mounts is False


def test_build_envelope_scoped_credentials_excluded_from_to_dict() -> None:
    env = build_envelope(
        tenant_id="t1",
        goal_id="g1",
        goal_text="test",
        scoped_llm_api_key="sk-secret-key",
        scoped_db_url="postgresql://user:pass@host/db",
    )
    d = env.to_dict()
    # Scoped credentials must NEVER appear in the serialised form
    serialised = str(d)
    assert "sk-secret-key" not in serialised
    assert "sk-secret" not in serialised


def test_build_envelope_runner_type_default_is_fake() -> None:
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    assert env.spec.runner_type == RunnerType.FAKE


def test_sign_envelope_is_idempotent() -> None:
    """Signing twice with the same key and fields produces the same signature."""
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    sig1 = env.signature
    sign_envelope(env)
    assert env.signature == sig1


def test_signing_key_override_via_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISOLATED_EXECUTION_SIGNING_KEY", "my-test-key-abc")
    env = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    assert verify_envelope(env) is True
    # Different key should fail
    monkeypatch.setenv("ISOLATED_EXECUTION_SIGNING_KEY", "wrong-key")
    # Must clear lru_cache on _get_signing_key if any — module reads os.environ directly
    # so no cache to clear; verify should fail immediately
    assert verify_envelope(env) is False
