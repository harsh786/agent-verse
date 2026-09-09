"""Tests verifying that feature flags preserve existing behaviour.

These are the most important tests in the execution-environment suite:
they prove that when ISOLATED_AGENT_EXECUTION=false (the default), the
existing code path is completely unchanged.
"""
from __future__ import annotations

import os

import pytest

# ── Flag-off: existing behaviour unchanged ────────────────────────────────────


def test_runtime_flags_isolated_execution_default_false() -> None:
    """All isolation flags default to False — existing behaviour is preserved."""
    # Ensure env vars are not set
    for var in (
        "ISOLATED_AGENT_EXECUTION",
        "ISOLATED_EXECUTION_REQUIRED",
        "ISOLATED_EXECUTION_LOCAL_RUNNER",
        "ISOLATED_EXECUTION_KUBERNETES_RUNNER",
    ):
        os.environ.pop(var, None)

    # Force reload of the cached flags
    from app.core.runtime_flags import get_runtime_flags
    get_runtime_flags.cache_clear()
    flags = get_runtime_flags()

    assert flags.isolated_agent_execution is False
    assert flags.isolated_execution_required is False
    assert flags.isolated_execution_local_runner is False
    assert flags.isolated_execution_kubernetes_runner is False

    # Restore the cache
    get_runtime_flags.cache_clear()


def test_settings_isolated_execution_default_false() -> None:
    """Settings model defaults match the flag spec."""
    from app.core.config import Settings
    s = Settings()
    assert s.isolated_agent_execution is False
    assert s.isolated_execution_required is False
    assert s.isolated_execution_local_runner is False
    assert s.isolated_execution_kubernetes_runner is False


async def test_agent_graph_runs_unchanged_when_flags_off() -> None:
    """The canonical graph runs in process when isolation flags are disabled."""
    from app.agent.graph import AgentGraph
    from app.agent.state import GoalStatus
    from app.providers.fake import FakeProvider
    from app.tenancy.context import PlanTier, TenantContext

    loop = AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["Step 1: Do the thing"]}']),
        executor=FakeProvider(responses=["Done"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "ok"}']),
    )
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")
    state = await loop.run(goal="test", tenant_ctx=ctx)
    assert state.status == GoalStatus.COMPLETE


def test_create_app_succeeds_and_registers_scheduler() -> None:
    """create_app() completes without error and exposes execution_scheduler."""
    from app.main import create_app
    app = create_app()
    # Scheduler is always registered (even when flags off, for future activation)
    assert hasattr(app.state, "execution_scheduler")
    # When all isolation flags are off, scheduler uses FakeRunner by default
    scheduler = app.state.execution_scheduler
    if scheduler is not None:
        assert scheduler.runner.runner_type == "fake"


# ── Flag-on: isolation path uses the same semantics ──────────────────────────


async def test_scheduler_result_status_matches_existing_status_vocabulary() -> None:
    """ExecutionResult.status values must match GoalStatus vocabulary."""
    from app.agent.state import GoalStatus
    from app.execution_environment.envelope import build_envelope
    from app.execution_environment.scheduler import ExecutionEnvironmentScheduler

    scheduler = ExecutionEnvironmentScheduler()
    envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test task")
    result = await scheduler.schedule(envelope)

    valid_statuses = {s.value for s in GoalStatus}
    assert result.status in valid_statuses, (
        f"result.status={result.status!r} is not a valid GoalStatus value"
    )


async def test_sandbox_dry_run_never_calls_real_tools_when_isolated() -> None:
    """Dry-run through isolated path must never touch real tools."""
    from app.execution_environment.envelope import build_envelope
    from app.execution_environment.scheduler import ExecutionEnvironmentScheduler

    tool_calls: list[str] = []

    def factory(envelope: object) -> object:
        """This factory must never be called in dry-run mode."""
        tool_calls.append("real_loop_constructed")
        raise AssertionError("Real agent loop constructed during dry-run!")

    from app.execution_environment.fake_runner import FakeRunner
    runner = FakeRunner(agent_loop_factory=factory)
    scheduler = ExecutionEnvironmentScheduler(runner=runner)

    envelope = build_envelope(
        tenant_id="t1", goal_id="g1", goal_text="test", dry_run=True
    )
    result = await scheduler.schedule(envelope)
    assert result.success is True
    assert tool_calls == []  # real loop must never have been touched


async def test_goal_service_does_not_use_isolation_when_flag_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GoalService._run_agent_loop falls through to in-process execution when flag off."""
    import os
    from unittest.mock import AsyncMock

    # Ensure isolation flag is off
    os.environ.pop("ISOLATED_AGENT_EXECUTION", None)
    from app.core.runtime_flags import get_runtime_flags
    get_runtime_flags.cache_clear()

    from app.providers.fake import FakeProvider
    from app.services.goal_service import GoalService
    from app.tenancy.context import PlanTier, TenantContext

    svc = GoalService()
    ctx = TenantContext(tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k1")

    # Patch _make_agent_loop_for_tenant to return a known FakeProvider loop
    from app.agent.graph import AgentGraph
    fake_loop = AgentGraph(
        planner=FakeProvider(responses=['{"steps": ["step1"]}']),
        executor=FakeProvider(responses=["ok"]),
        verifier=FakeProvider(responses=['{"success": true, "reason": "done"}']),
    )
    monkeypatch.setattr(svc, "_make_agent_loop_for_tenant", lambda *a, **kw: fake_loop)
    monkeypatch.setattr(svc, "_build_tool_context", AsyncMock(return_value=None))

    # Insert a dummy goal record
    from datetime import UTC, datetime

    from app.agent.state import GoalStatus
    from app.services.goal_service import GoalRecord
    record = GoalRecord(
        goal_id="g1",
        goal_text="test",
        status=GoalStatus.PLANNING,
        tenant_id="t1",
        priority="normal",
        dry_run=False,
        created_at=datetime.now(UTC).isoformat(),
    )
    svc._goals["g1"] = record

    # _run_agent_loop should NOT call _run_agent_loop_isolated
    isolated_called = False
    original_isolated = svc._run_agent_loop_isolated

    async def spy(*args: object, **kwargs: object) -> None:
        nonlocal isolated_called
        isolated_called = True
        return await original_isolated(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(svc, "_run_agent_loop_isolated", spy)

    await svc._run_agent_loop("g1", "test", ctx)

    assert isolated_called is False, (
        "_run_agent_loop_isolated must NOT be called when ISOLATED_AGENT_EXECUTION=false"
    )

    get_runtime_flags.cache_clear()
