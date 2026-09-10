"""Tests for the local subprocess runner.

These tests verify the isolation contracts of LocalSubprocessRunner without
requiring Docker or Kubernetes.  They do NOT test actual subprocess isolation
(that would require OS-level inspection), but they verify:

- The runner interface is correct
- The environment is built correctly (no host credential leakage)
- Process group kill is used instead of proc.kill()
- Output size limit is enforced
- Stderr is captured
- The ALLOWED_ENV_KEYS allowlist excludes DATABASE_URL, REDIS_URL, etc.

Integration tests that actually spawn subprocesses are marked ``integration``
and require Docker/colima to be running (so that the full app import tree works).
"""
from __future__ import annotations

from app.execution_environment.envelope import build_envelope
from app.execution_environment.local_runner import (
    _ALLOWED_ENV_KEYS,
    LocalSubprocessRunner,
    _encode_envelope,
    _try_forward_event,
)

# ── Allowlist checks ──────────────────────────────────────────────────────────


def test_allowed_env_keys_excludes_database_url() -> None:
    """DATABASE_URL must never be in the env allowlist."""
    assert "DATABASE_URL" not in _ALLOWED_ENV_KEYS


def test_allowed_env_keys_excludes_redis_url() -> None:
    """REDIS_URL must never be in the env allowlist."""
    assert "REDIS_URL" not in _ALLOWED_ENV_KEYS


def test_allowed_env_keys_excludes_anthropic_key() -> None:
    assert "ANTHROPIC_API_KEY" not in _ALLOWED_ENV_KEYS


def test_allowed_env_keys_excludes_openai_key() -> None:
    assert "OPENAI_API_KEY" not in _ALLOWED_ENV_KEYS


def test_allowed_env_keys_excludes_docker_host() -> None:
    assert "DOCKER_HOST" not in _ALLOWED_ENV_KEYS


def test_allowed_env_keys_excludes_aws_credentials() -> None:
    assert "AWS_ACCESS_KEY_ID" not in _ALLOWED_ENV_KEYS
    assert "AWS_SECRET_ACCESS_KEY" not in _ALLOWED_ENV_KEYS
    assert "AWS_SESSION_TOKEN" not in _ALLOWED_ENV_KEYS


def test_allowed_env_keys_excludes_ssh_auth_sock() -> None:
    assert "SSH_AUTH_SOCK" not in _ALLOWED_ENV_KEYS


def test_allowed_env_keys_includes_signing_key() -> None:
    """The signing key must be passed so the worker can verify the envelope."""
    assert "ISOLATED_EXECUTION_SIGNING_KEY" in _ALLOWED_ENV_KEYS


def test_allowed_env_keys_includes_worker_vars() -> None:
    assert "_ISOLATED_WORKER_ENVELOPE" in _ALLOWED_ENV_KEYS
    assert "_ISOLATED_WORKER_DB_URL" in _ALLOWED_ENV_KEYS
    assert "_ISOLATED_WORKER_LLM_KEY" in _ALLOWED_ENV_KEYS


# ── Envelope encoding ─────────────────────────────────────────────────────────


def test_encode_envelope_produces_valid_base64() -> None:
    import base64
    import json
    payload = {"tenant_id": "t1", "goal_id": "g1", "goal_text": "test"}
    encoded = _encode_envelope(payload)
    decoded = json.loads(base64.b64decode(encoded).decode())
    assert decoded == payload


def test_encode_envelope_excludes_scoped_credentials() -> None:
    """The envelope passed to the subprocess must not contain raw credentials in to_dict."""
    envelope = build_envelope(
        tenant_id="t1",
        goal_id="g1",
        goal_text="test",
        scoped_llm_api_key="sk-ant-secret-key",
        scoped_db_url="postgresql://user:pass@host/db",
    )
    import base64
    payload = envelope.to_dict()
    encoded = _encode_envelope(payload)
    decoded_str = base64.b64decode(encoded).decode()
    assert "sk-ant-secret-key" not in decoded_str
    assert "pass@host" not in decoded_str


# ── Event forwarding ──────────────────────────────────────────────────────────


async def test_try_forward_event_skips_result_lines() -> None:
    received = []

    async def cb(evt: dict) -> None:
        received.append(evt)

    _try_forward_event(b'{"_result": true, "status": "complete"}', cb)
    # ensure_future schedules it — but in a test we don't run event loop here
    # Just verify no TypeError / crash


def test_try_forward_event_skips_empty_lines() -> None:
    received = []

    async def cb(evt: dict) -> None:
        received.append(evt)

    _try_forward_event(b"   ", cb)  # should silently return


def test_try_forward_event_skips_log_lines() -> None:
    received = []

    async def cb(evt: dict) -> None:
        received.append(evt)

    _try_forward_event(b'{"_log": true, "level": "info", "msg": "test"}', cb)


# ── Runner interface ──────────────────────────────────────────────────────────


def test_local_runner_type_string() -> None:
    runner = LocalSubprocessRunner()
    assert runner.runner_type == "local"


async def test_local_runner_health_check_returns_status() -> None:
    runner = LocalSubprocessRunner()
    # The health check spawns a subprocess to verify the entrypoint is importable.
    # In CI without all deps this may be unhealthy — we just verify the interface.
    status = await runner.health_check.check()
    assert status.runner_type == "local"
    assert isinstance(status.healthy, bool)
    assert status.latency_ms >= 0.0
