"""Tests for worker_entrypoint — the subprocess execution entry point.

Tests verify:
- Envelope is decoded correctly from base64 env var
- HMAC verification blocks tampered envelopes
- Dry-run path emits correct events and returns success
- Resource limit setup is called (best-effort; failure is non-fatal)
- Provider selection by key prefix
- Result structure is correct

These tests invoke main() directly in-process (not as a real subprocess) by
manipulating os.environ, so they are fast unit tests with no Docker dependency.
"""
from __future__ import annotations

import base64
import json
import os
from io import StringIO
from unittest.mock import patch

import pytest

from app.execution_environment.envelope import build_envelope, sign_envelope
from app.execution_environment.worker_entrypoint import _build_provider, _set_resource_limits


# ── _build_provider ───────────────────────────────────────────────────────────


def test_build_provider_returns_fake_when_no_key() -> None:
    from app.providers.fake import FakeProvider
    p = _build_provider("", "planner")
    assert isinstance(p, FakeProvider)


def test_build_provider_returns_separate_instances_per_role() -> None:
    """Each role must get its own instance to prevent cross-role state sharing."""
    p1 = _build_provider("", "planner")
    p2 = _build_provider("", "executor")
    assert p1 is not p2


def test_build_provider_anthropic_key_prefix() -> None:
    """sk-ant- prefix → try AnthropicProvider (may fail if not installed; falls back)."""
    p = _build_provider("sk-ant-test-key", "planner")
    # Either AnthropicProvider or FakeProvider (fallback) — both are valid
    assert p is not None


def test_build_provider_unknown_prefix_falls_back_to_fake() -> None:
    from app.providers.fake import FakeProvider
    p = _build_provider("UNKNOWN-KEY-PREFIX", "planner")
    assert isinstance(p, FakeProvider)


# ── _set_resource_limits ──────────────────────────────────────────────────────


def test_set_resource_limits_does_not_raise_on_zero_memory() -> None:
    """memory_mb=0 must be skipped gracefully (no setrlimit call)."""
    _set_resource_limits(memory_mb=0, cpu_seconds=0)  # must not raise


def test_set_resource_limits_does_not_raise_on_import_error() -> None:
    """If resource module is unavailable, must not raise."""
    with patch.dict("sys.modules", {"resource": None}):
        _set_resource_limits(memory_mb=256)  # must not raise


# ── main() — envelope decode and HMAC check ───────────────────────────────────


def _run_main_with_env(extra_env: dict) -> tuple[list[dict], int]:
    """Run main() with injected env vars, capture stdout events."""
    import io
    from contextlib import redirect_stdout

    from app.execution_environment import worker_entrypoint as we

    events: list[dict] = []
    original_emit = we._emit

    def capture_emit(event: dict) -> None:
        events.append(event)

    we._emit = capture_emit  # type: ignore[method-assign]
    try:
        with patch.dict(os.environ, extra_env, clear=False):
            rc = we.main()
    finally:
        we._emit = original_emit  # type: ignore[method-assign]
    return events, rc


def test_main_fails_without_envelope() -> None:
    env = {"_ISOLATED_WORKER_ENVELOPE": ""}
    events, rc = _run_main_with_env(env)
    assert rc == 1
    assert any(e.get("type") == "worker_error" for e in events)


def test_main_fails_on_invalid_base64() -> None:
    env = {"_ISOLATED_WORKER_ENVELOPE": "!!!not-valid-base64!!!"}
    events, rc = _run_main_with_env(env)
    assert rc == 1
    error_events = [e for e in events if e.get("type") == "worker_error"]
    assert error_events


def test_main_rejects_tampered_envelope() -> None:
    """A tampered envelope (goal_text changed after signing) must be rejected."""
    envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="original goal")
    payload = envelope.to_dict()
    payload["goal_text"] = "TAMPERED GOAL"  # tamper after to_dict (signature not updated)
    payload["signature"] = envelope.signature  # keep original signature

    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    events, rc = _run_main_with_env({"_ISOLATED_WORKER_ENVELOPE": encoded})
    assert rc == 1
    error_events = [e for e in events if e.get("type") == "worker_error"]
    assert error_events
    assert any("hmac" in str(e.get("reason", "")).lower() or "tamper" in str(e.get("reason", "")).lower()
               for e in error_events)


def test_main_dry_run_succeeds_with_valid_envelope() -> None:
    """A valid signed dry-run envelope must emit goal_complete and return 0."""
    envelope = build_envelope(
        tenant_id="t1", goal_id="g1", goal_text="test dry run", dry_run=True
    )
    payload = {**envelope.to_dict(), "signature": envelope.signature}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()

    events, rc = _run_main_with_env({"_ISOLATED_WORKER_ENVELOPE": encoded})
    assert rc == 0
    event_types = [e.get("type") for e in events]
    assert "goal_started" in event_types
    assert "dry_run_preview" in event_types
    assert "goal_complete" in event_types
    result_events = [e for e in events if e.get("_result")]
    assert result_events
    assert result_events[-1]["success"] is True


def test_main_dry_run_never_touches_agent_graph() -> None:
    """Dry-run must short-circuit before the execution graph is constructed."""
    import app.agent.graph as graph_mod

    original_graph_class = graph_mod.AgentGraph
    graph_constructed = []

    class TrackingGraph(original_graph_class):  # type: ignore[valid-type]
        def __init__(self, **kwargs):  # type: ignore[override]
            graph_constructed.append(True)
            super().__init__(**kwargs)

    envelope = build_envelope(
        tenant_id="t1", goal_id="g1", goal_text="dry run test", dry_run=True
    )
    payload = {**envelope.to_dict(), "signature": envelope.signature}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()

    with patch.object(graph_mod, "AgentGraph", TrackingGraph):
        _run_main_with_env({"_ISOLATED_WORKER_ENVELOPE": encoded})

    assert graph_constructed == []
