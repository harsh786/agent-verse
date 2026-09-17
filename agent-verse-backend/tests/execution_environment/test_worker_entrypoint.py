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
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.execution_environment.envelope import build_envelope
from app.execution_environment.worker_entrypoint import (
    _build_provider,
    _emit,
    _emit_log,
    _make_db_factory,
    _set_resource_limits,
)

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


# ── _emit / _emit_log ──────────────────────────────────────────────────────────


def test_emit_writes_json_line_without_raising(capsys) -> None:
    _emit({"type": "worker_started", "goal": "x"})
    out = capsys.readouterr().out
    assert "worker_started" in out


def test_emit_suppresses_serialization_errors() -> None:
    # A non-JSON-serializable value must not raise — the event is just dropped.
    _emit({"type": "bad", "value": object()})


def test_emit_log_writes_to_stderr(capsys) -> None:
    _emit_log("info", "hello", goal_id="g1")
    err = capsys.readouterr().err
    assert "hello" in err
    assert "_log" in err


def test_emit_log_suppresses_serialization_errors() -> None:
    # A non-JSON-serializable extra kwarg must not raise.
    _emit_log("warning", "boom", bad=object())


# ── _set_resource_limits — setrlimit failure branches ─────────────────────────


def test_set_resource_limits_memory_setrlimit_oserror_is_logged() -> None:
    with patch("resource.setrlimit", side_effect=OSError("nope")):
        _set_resource_limits(memory_mb=256, cpu_seconds=0, max_processes=0)  # must not raise


def test_set_resource_limits_cpu_setrlimit_valueerror_is_logged() -> None:
    with patch("resource.setrlimit", side_effect=ValueError("bad value")):
        _set_resource_limits(memory_mb=0, cpu_seconds=30, max_processes=0)  # must not raise


def test_set_resource_limits_nproc_setrlimit_oserror_is_logged() -> None:
    with patch("resource.setrlimit", side_effect=OSError("nope")):
        _set_resource_limits(memory_mb=0, cpu_seconds=0, max_processes=10)  # must not raise


def test_set_resource_limits_unexpected_exception_is_swallowed() -> None:
    # A non-OSError/ValueError exception from setrlimit hits the catch-all.
    with patch("resource.setrlimit", side_effect=RuntimeError("totally unexpected")):
        _set_resource_limits(memory_mb=256, cpu_seconds=10, max_processes=5)  # must not raise


# ── _build_provider — other key prefixes and init-failure fallbacks ───────────


def test_build_provider_openai_key_prefix() -> None:
    p = _build_provider("sk-openai-test-key", "executor")
    assert p is not None


def test_build_provider_gemini_key_prefix() -> None:
    p = _build_provider("AIzaSyTestKey", "planner")
    assert p is not None


def test_build_provider_voyage_key_prefix() -> None:
    p = _build_provider("pa-test-key", "verifier")
    assert p is not None


def test_build_provider_anthropic_init_failure_falls_back_to_fake() -> None:
    from app.providers.fake import FakeProvider

    with patch(
        "app.providers.anthropic_provider.AnthropicProvider.__init__",
        side_effect=RuntimeError("init failed"),
    ):
        p = _build_provider("sk-ant-test-key", "planner")
    assert isinstance(p, FakeProvider)


def test_build_provider_openai_init_failure_falls_back_to_fake() -> None:
    from app.providers.fake import FakeProvider

    with patch(
        "app.providers.openai_compatible.OpenAICompatibleProvider.__init__",
        side_effect=RuntimeError("init failed"),
    ):
        p = _build_provider("sk-openai-test-key", "executor")
    assert isinstance(p, FakeProvider)


def test_build_provider_gemini_init_failure_falls_back_to_fake() -> None:
    from app.providers.fake import FakeProvider

    with patch(
        "app.providers.gemini_provider.GeminiProvider.__init__",
        side_effect=RuntimeError("init failed"),
    ):
        p = _build_provider("AIzaSyTestKey", "planner")
    assert isinstance(p, FakeProvider)


def test_build_provider_voyage_init_failure_falls_back_to_fake() -> None:
    from app.providers.fake import FakeProvider

    with patch(
        "app.providers.voyage_provider.VoyageProvider.__init__",
        side_effect=RuntimeError("init failed"),
    ):
        p = _build_provider("pa-test-key", "verifier")
    assert isinstance(p, FakeProvider)


# ── _make_db_factory ───────────────────────────────────────────────────────────


def test_make_db_factory_returns_none_for_empty_url() -> None:
    assert _make_db_factory("", "t1") is None


def test_make_db_factory_returns_factory_on_success() -> None:
    dummy_factory = MagicMock()
    with patch("app.db.session._make_session_factory", return_value=dummy_factory):
        result = _make_db_factory("postgresql://x/y", "t1")
    assert result is dummy_factory


def test_make_db_factory_returns_none_on_error() -> None:
    with patch(
        "app.db.session._make_session_factory",
        side_effect=RuntimeError("db init failed"),
    ):
        result = _make_db_factory("postgresql://x/y", "t1")
    assert result is None


# ── main() — envelope reconstruction edge cases ───────────────────────────────


def test_main_no_signature_is_rejected() -> None:
    envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="goal", dry_run=True)
    payload = envelope.to_dict()
    payload["signature"] = ""  # no signature at all

    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    events, rc = _run_main_with_env({"_ISOLATED_WORKER_ENVELOPE": encoded})
    assert rc == 1
    error_events = [e for e in events if e.get("type") == "worker_error"]
    assert error_events
    assert any("signature" in str(e.get("reason", "")).lower() for e in error_events)


def test_main_invalid_policy_enum_values_fall_back_to_defaults() -> None:
    """Garbage enum strings in the policy dict hit the ValueError fallbacks.

    build_envelope's own defaults (deny_all / read_only_root / standard / fake)
    equal the ValueError fallback defaults in worker_entrypoint, so the
    reconstructed envelope still matches the original signature.
    """
    envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="goal", dry_run=True)
    payload = envelope.to_dict()
    payload["signature"] = envelope.signature
    payload["policy"]["network_policy"] = "not-a-real-network-policy"
    payload["policy"]["filesystem_policy"] = "not-a-real-fs-policy"
    payload["policy"]["audit_level"] = "not-a-real-audit-level"
    payload["spec"]["runner_type"] = "not-a-real-runner-type"

    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    events, rc = _run_main_with_env({"_ISOLATED_WORKER_ENVELOPE": encoded})
    assert rc == 0
    result_events = [e for e in events if e.get("_result")]
    assert result_events
    assert result_events[-1]["success"] is True


def test_main_verification_infrastructure_error_fails_closed() -> None:
    envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="goal", dry_run=True)
    payload = {**envelope.to_dict(), "signature": envelope.signature}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()

    with patch(
        "app.execution_environment.envelope.verify_envelope",
        side_effect=RuntimeError("verification blew up"),
    ):
        events, rc = _run_main_with_env({"_ISOLATED_WORKER_ENVELOPE": encoded})
    assert rc == 1
    error_events = [e for e in events if e.get("type") == "worker_error"]
    assert error_events
    assert any("verification error" in str(e.get("reason", "")).lower() for e in error_events)


# ── main() — non-dry-run execution path ───────────────────────────────────────


def _signed_payload(**kwargs) -> str:
    envelope = build_envelope(tenant_id="t1", goal_id="g1", **kwargs)
    payload = {**envelope.to_dict(), "signature": envelope.signature}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_main_non_dry_run_success_returns_complete_result() -> None:
    import app.agent.graph as graph_mod
    from app.agent.state import GoalStatus

    fake_step = SimpleNamespace(
        description="do it", output="done", status=SimpleNamespace(value="complete")
    )
    fake_state = SimpleNamespace(
        status=GoalStatus.COMPLETE,
        iterations=2,
        plan=["step1"],
        steps=[fake_step],
        verification_feedback="looks good",
    )
    fake_instance = SimpleNamespace(run=AsyncMock(return_value=fake_state))
    fake_graph_cls = MagicMock(return_value=fake_instance)

    encoded = _signed_payload(goal_text="do the thing", dry_run=False)

    with patch.object(graph_mod, "AgentGraph", fake_graph_cls):
        events, rc = _run_main_with_env({"_ISOLATED_WORKER_ENVELOPE": encoded})

    assert rc == 0
    result_events = [e for e in events if e.get("_result")]
    assert result_events
    result = result_events[-1]
    assert result["success"] is True
    assert result["status"] == "complete"
    assert result["iterations"] == 2
    assert result["steps"][0]["description"] == "do it"
    assert result["verification_feedback"] == "looks good"


def test_main_non_dry_run_failure_status_returns_success_false() -> None:
    import app.agent.graph as graph_mod
    from app.agent.state import GoalStatus

    fake_state = SimpleNamespace(
        status=GoalStatus.FAILED,
        iterations=3,
        plan=[],
        steps=[],
        verification_feedback="could not complete",
    )
    fake_instance = SimpleNamespace(run=AsyncMock(return_value=fake_state))
    fake_graph_cls = MagicMock(return_value=fake_instance)

    encoded = _signed_payload(goal_text="do the thing", dry_run=False)

    with patch.object(graph_mod, "AgentGraph", fake_graph_cls):
        events, rc = _run_main_with_env({"_ISOLATED_WORKER_ENVELOPE": encoded})

    assert rc == 0  # worker itself completed cleanly; the goal failed
    result_events = [e for e in events if e.get("_result")]
    assert result_events[-1]["success"] is False
    assert result_events[-1]["status"] == "failed"


def test_main_non_dry_run_agent_graph_exception_is_reported() -> None:
    import app.agent.graph as graph_mod

    fake_instance = SimpleNamespace(run=AsyncMock(side_effect=RuntimeError("graph exploded")))
    fake_graph_cls = MagicMock(return_value=fake_instance)

    encoded = _signed_payload(goal_text="do the thing", dry_run=False)

    with patch.object(graph_mod, "AgentGraph", fake_graph_cls):
        events, rc = _run_main_with_env({"_ISOLATED_WORKER_ENVELOPE": encoded})

    assert rc == 1
    result_events = [e for e in events if e.get("_result")]
    assert result_events
    assert result_events[-1]["success"] is False
    assert result_events[-1]["status"] == "failed"
    assert "graph exploded" in result_events[-1]["error_message"]


def test_main_non_dry_run_propagates_plan_tier_from_agent_config() -> None:
    """agent_config.plan is parsed into a real PlanTier for the tenant context."""
    import app.agent.graph as graph_mod
    from app.agent.state import GoalStatus

    captured_ctx: list = []

    class _CapturingGraph:
        def __init__(self, **kwargs) -> None:
            pass

        async def run(self, *, goal, tenant_ctx, initial_context, event_callback):
            captured_ctx.append(tenant_ctx)
            return SimpleNamespace(
                status=GoalStatus.COMPLETE,
                iterations=1,
                plan=[],
                steps=[],
                verification_feedback="",
            )

    envelope = build_envelope(
        tenant_id="t1",
        goal_id="g1",
        goal_text="goal",
        dry_run=False,
        agent_config={"plan": "enterprise"},
    )
    payload = {**envelope.to_dict(), "signature": envelope.signature}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()

    with patch.object(graph_mod, "AgentGraph", _CapturingGraph):
        events, rc = _run_main_with_env({"_ISOLATED_WORKER_ENVELOPE": encoded})

    assert rc == 0
    assert captured_ctx
    assert captured_ctx[0].plan.value == "enterprise"


class _NullAsyncCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _FakeAsyncSession:
    """Minimal AsyncSession stand-in that records every executed statement."""

    def __init__(self) -> None:
        self.executed: list[tuple[Any, Any]] = []
        self.bind = None  # forces the non-postgres (select-then-write) branch

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.executed.append((stmt, params))
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    def add(self, obj: Any) -> None:
        pass

    def begin(self) -> _NullAsyncCtx:
        return _NullAsyncCtx()


class _SessionCtx:
    """Async context manager yielding a fixed fake session (dunders must live
    on the class, not the instance — `async with` looks them up via type())."""

    def __init__(self, session: _FakeAsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> _FakeAsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: Any) -> bool:
        return False


class _FakeSessionFactory:
    """Stand-in for the callable returned by app.db.session._make_session_factory."""

    def __init__(self, session: _FakeAsyncSession) -> None:
        self._session = session

    def __call__(self) -> _SessionCtx:
        return _SessionCtx(self._session)


def test_main_non_dry_run_wires_db_factory_into_agent_graph() -> None:
    """The factory built from _ISOLATED_WORKER_DB_URL must be attached to the
    AgentGraph instance as `_db_session_factory` — previously it was built and
    discarded, silently disabling checkpoint persistence for isolated-worker runs."""
    import app.agent.graph as graph_mod
    from app.agent.state import GoalStatus

    sentinel_factory = object()
    captured: list[Any] = []

    class _CapturingGraph:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(self)

        async def run(self, *, goal, tenant_ctx, initial_context, event_callback):
            return SimpleNamespace(
                status=GoalStatus.COMPLETE,
                iterations=1,
                plan=[],
                steps=[],
                verification_feedback="",
            )

    encoded = _signed_payload(goal_text="do the thing", dry_run=False)

    with (
        patch.object(graph_mod, "AgentGraph", _CapturingGraph),
        patch(
            "app.execution_environment.worker_entrypoint._make_db_factory",
            return_value=sentinel_factory,
        ),
    ):
        events, rc = _run_main_with_env(
            {"_ISOLATED_WORKER_ENVELOPE": encoded, "_ISOLATED_WORKER_DB_URL": "postgresql://x/y"}
        )

    assert rc == 0
    assert captured
    assert captured[0]._db_session_factory is sentinel_factory


def test_main_non_dry_run_leaves_db_session_factory_none_without_db_url() -> None:
    """No _ISOLATED_WORKER_DB_URL set -> _db_factory is None -> wired through as None
    (AgentGraph's own `if self._db_session_factory is None: return` early-return
    already makes this a safe no-op for checkpointing)."""
    import app.agent.graph as graph_mod
    from app.agent.state import GoalStatus

    captured: list[Any] = []

    class _CapturingGraph:
        def __init__(self, **kwargs: Any) -> None:
            captured.append(self)

        async def run(self, *, goal, tenant_ctx, initial_context, event_callback):
            return SimpleNamespace(
                status=GoalStatus.COMPLETE, iterations=1, plan=[], steps=[], verification_feedback=""
            )

    encoded = _signed_payload(goal_text="do the thing", dry_run=False)

    with patch.object(graph_mod, "AgentGraph", _CapturingGraph):
        events, rc = _run_main_with_env({"_ISOLATED_WORKER_ENVELOPE": encoded})

    assert rc == 0
    assert captured
    assert captured[0]._db_session_factory is None


async def test_write_checkpoint_sets_rls_guc_on_session_from_wired_factory() -> None:
    """End-to-end (minus a real DB): a session obtained through the factory
    that worker_entrypoint now wires into AgentGraph._db_session_factory must
    have `app.tenant_id` set via SET LOCAL / set_config before the checkpoint
    write, exactly as app/db/rls.sqlalchemy_rls_context does."""
    from app.agent.graph import AgentGraph
    from app.providers.fake import FakeProvider

    session = _FakeAsyncSession()
    factory = _FakeSessionFactory(session)

    graph = AgentGraph(
        planner=FakeProvider(responses=[]),
        executor=FakeProvider(responses=[]),
        verifier=FakeProvider(responses=[]),
    )
    # Simulates the fix: worker_entrypoint._run() now sets this after
    # constructing AgentGraph, instead of leaving _db_factory unused.
    graph._db_session_factory = factory

    tenant_ctx = SimpleNamespace(tenant_id="tenant-rls-check")
    fake_state = SimpleNamespace(plan=["step one"], iterations=1)

    await graph._write_checkpoint(
        goal_id="goal-1", step_index=0, state=fake_state, tenant_ctx=tenant_ctx
    )

    rls_calls = [
        params
        for (_stmt, params) in session.executed
        if params and params.get("tid") == "tenant-rls-check"
    ]
    assert rls_calls, (
        f"expected a set_config('app.tenant_id', ...) call for tenant-rls-check, "
        f"got executed statements: {session.executed}"
    )


def test_main_non_dry_run_invalid_plan_tier_falls_back_to_professional() -> None:
    import app.agent.graph as graph_mod
    from app.agent.state import GoalStatus

    captured_ctx: list = []

    class _CapturingGraph:
        def __init__(self, **kwargs) -> None:
            pass

        async def run(self, *, goal, tenant_ctx, initial_context, event_callback):
            captured_ctx.append(tenant_ctx)
            return SimpleNamespace(
                status=GoalStatus.COMPLETE,
                iterations=1,
                plan=[],
                steps=[],
                verification_feedback="",
            )

    envelope = build_envelope(
        tenant_id="t1",
        goal_id="g1",
        goal_text="goal",
        dry_run=False,
        agent_config={"plan": "not-a-real-plan"},
    )
    payload = {**envelope.to_dict(), "signature": envelope.signature}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()

    with patch.object(graph_mod, "AgentGraph", _CapturingGraph):
        _run_main_with_env({"_ISOLATED_WORKER_ENVELOPE": encoded})

    assert captured_ctx
    assert captured_ctx[0].plan.value == "professional"
