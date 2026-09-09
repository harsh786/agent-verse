"""Tests for the FakeRunner and ExecutionEnvironmentScheduler.

These tests verify:
1. The fake runner runs agent execution with the same result shape as the
   existing in-process path.
2. The scheduler routes to the correct runner and enforces fail-closed.
3. Sandbox / dry-run modes still never call real tools.
4. Runner unavailable → structured error (never silent fallback).
5. Policy denial → structured error.
"""
from __future__ import annotations

import pytest

from app.execution_environment.envelope import build_envelope
from app.execution_environment.fake_runner import FakeRunner
from app.execution_environment.health import AlwaysUnhealthyCheck
from app.execution_environment.models import (
    ExecutionFailureReason,
    ExecutionRequest,
    RunnerType,
)
from app.execution_environment.scheduler import (
    ExecutionEnvironmentScheduler,
    RunnerUnavailableError,
)

# ── FakeRunner tests ──────────────────────────────────────────────────────────


async def test_fake_runner_completes_simple_goal() -> None:
    runner = FakeRunner()
    envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="Do something")
    request = ExecutionRequest(envelope=envelope, runner_type=RunnerType.FAKE)
    result = await runner.run(request)
    assert result.success is True
    assert result.status == "complete"
    assert result.runner_type == "fake"
    assert result.capsule_id.startswith("fake-")


async def test_fake_runner_result_shape_matches_existing_path() -> None:
    """The result must contain the same fields as the existing AgentState-based path."""
    runner = FakeRunner()
    envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="Multi-step task")
    request = ExecutionRequest(envelope=envelope, runner_type=RunnerType.FAKE)
    result = await runner.run(request)
    assert isinstance(result.plan, list)
    assert isinstance(result.steps, list)
    assert isinstance(result.iterations, int)
    assert isinstance(result.verification_feedback, str)


async def test_fake_runner_dry_run_never_executes_real_loop() -> None:
    """Dry-run must short-circuit without calling the agent loop."""
    loop_called = False

    def factory(envelope: object) -> object:
        nonlocal loop_called
        loop_called = True
        raise AssertionError("AgentLoop should not be called in dry-run mode")

    runner = FakeRunner(agent_loop_factory=factory)
    envelope = build_envelope(
        tenant_id="t1", goal_id="g1", goal_text="test", dry_run=True
    )
    request = ExecutionRequest(envelope=envelope, runner_type=RunnerType.FAKE)
    result = await runner.run(request)
    assert result.success is True
    assert loop_called is False


async def test_fake_runner_dry_run_emits_expected_events() -> None:
    runner = FakeRunner()
    envelope = build_envelope(
        tenant_id="t1", goal_id="g1", goal_text="test", dry_run=True
    )
    request = ExecutionRequest(envelope=envelope, runner_type=RunnerType.FAKE)
    collected: list[dict] = []

    async def callback(event: dict) -> None:
        collected.append(event)

    await runner.run(request, event_callback=callback)
    types = [e["type"] for e in collected]
    assert "goal_started" in types
    assert "dry_run_preview" in types
    assert "goal_complete" in types


async def test_fake_runner_forwards_events_to_callback() -> None:
    runner = FakeRunner()
    envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="stream test")
    request = ExecutionRequest(envelope=envelope, runner_type=RunnerType.FAKE)
    collected: list[dict] = []

    async def callback(event: dict) -> None:
        collected.append(event)

    await runner.run(request, event_callback=callback)
    assert any(e.get("type") == "goal_complete" for e in collected)


async def test_fake_runner_rejects_invalid_signature() -> None:
    envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="test")
    envelope.signature = "invalid-sig"
    runner = FakeRunner()
    request = ExecutionRequest(envelope=envelope, runner_type=RunnerType.FAKE)
    result = await runner.run(request)
    assert result.success is False
    # HMAC failure uses SANDBOX_VIOLATION (not POLICY_DENIED) for clarity in audit
    assert result.failure_reason == ExecutionFailureReason.SANDBOX_VIOLATION


async def test_fake_runner_health_check_is_always_healthy() -> None:
    runner = FakeRunner()
    status = await runner.health_check.check()
    assert status.healthy is True


async def test_fake_runner_receives_same_runtime_profile() -> None:
    """The envelope must carry the runtime_profile so the runner can use it."""
    profile = {"complexity": "high", "risk": "medium", "rag_strategy": "raptor"}
    envelope = build_envelope(
        tenant_id="t1",
        goal_id="g1",
        goal_text="complex task",
        runtime_profile=profile,
    )
    assert envelope.runtime_profile == profile


async def test_fake_runner_receives_same_execution_context() -> None:
    ctx = {"tool_prompt": "use GitHub", "model_override": "claude-opus-4-8"}
    envelope = build_envelope(
        tenant_id="t1",
        goal_id="g1",
        goal_text="task",
        execution_context=ctx,
    )
    assert envelope.execution_context == ctx


# ── Scheduler tests ───────────────────────────────────────────────────────────


async def test_scheduler_dispatches_to_fake_runner() -> None:
    scheduler = ExecutionEnvironmentScheduler()
    envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="task")
    result = await scheduler.schedule(envelope)
    assert result.success is True
    assert result.runner_type == "fake"


async def test_scheduler_fail_closed_when_runner_unavailable() -> None:
    """An unhealthy runner MUST raise RunnerUnavailableError (never silent fallback)."""
    from app.execution_environment.health import RunnerHealthCheck

    class UnhealthyFakeRunner(FakeRunner):
        @property
        def health_check(self) -> RunnerHealthCheck:
            return AlwaysUnhealthyCheck(runner_type="fake", message="Simulated outage")

    scheduler = ExecutionEnvironmentScheduler(runner=UnhealthyFakeRunner())
    envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="task")

    with pytest.raises(RunnerUnavailableError) as exc_info:
        await scheduler.schedule(envelope)

    assert exc_info.value.failure_reason == ExecutionFailureReason.RUNNER_UNAVAILABLE
    assert "unhealthy" in str(exc_info.value).lower()


async def test_scheduler_fail_closed_on_policy_denial() -> None:
    scheduler = ExecutionEnvironmentScheduler()
    envelope = build_envelope(tenant_id="t1", goal_id="g1", goal_text="task")
    envelope.policy.allow_privileged = True  # policy violation

    with pytest.raises(RunnerUnavailableError) as exc_info:
        await scheduler.schedule(envelope)

    assert exc_info.value.failure_reason == ExecutionFailureReason.POLICY_DENIED


async def test_scheduler_from_flags_default_is_fake() -> None:
    scheduler = ExecutionEnvironmentScheduler.from_flags()
    assert scheduler.runner.runner_type == "fake"


async def test_scheduler_from_flags_local_selects_local_runner() -> None:
    scheduler = ExecutionEnvironmentScheduler.from_flags(
        isolated_execution_local_runner=True,
    )
    assert scheduler.runner.runner_type == "local"


async def test_scheduler_from_flags_k8s_selects_kubernetes_runner() -> None:
    scheduler = ExecutionEnvironmentScheduler.from_flags(
        isolated_execution_kubernetes_runner=True,
    )
    assert scheduler.runner.runner_type == "kubernetes"
