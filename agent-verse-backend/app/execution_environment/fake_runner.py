"""Fake in-process runner — used for unit tests and when no real runner is configured.

This runner does NOT provide OS-level process isolation.  It runs the existing
AgentLoop directly in the current asyncio event loop, which makes it suitable
for:
  - Unit tests verifying envelope construction and routing
  - CI environments without Docker or Kubernetes
  - Local development when isolation is not required

The fake runner DOES validate the full isolation contract:
  - Envelope HMAC signature verified before execution
  - Policy evaluation enforced (deny privileged, deny host mounts, etc.)
  - Behaviour identical to the existing _run_agent_loop path
  - Events forwarded through the same callback chain with isolation metadata
  - asyncio.CancelledError caught and surfaced as a structured result
"""
from __future__ import annotations

import contextlib
import logging
import time
import uuid
from typing import Any

from app.execution_environment.envelope import verify_envelope
from app.execution_environment.events import make_forwarding_callback
from app.execution_environment.health import AlwaysHealthyCheck, RunnerHealthCheck
from app.execution_environment.models import (
    ExecutionFailureReason,
    ExecutionRequest,
    ExecutionResult,
    RunnerType,
)
from app.execution_environment.policy import evaluate_policy
from app.execution_environment.runner_client import BaseRunner

logger = logging.getLogger(__name__)


class FakeRunner(BaseRunner):
    """In-process fake runner.

    Runs the existing :class:`~app.agent.loop.AgentLoop` or any pre-configured
    agent runner directly in the current process.  Isolation semantics are
    contract-validated but not OS-enforced.

    Args:
        agent_loop_factory: Optional callable that accepts the envelope and
            returns an object with a ``run(goal, tenant_ctx, ...)`` coroutine.
            When ``None``, the runner constructs a minimal :class:`AgentLoop`
            with independent :class:`FakeProvider` instances per role (one
            instance per role — no shared state between planner/executor/verifier).
    """

    def __init__(self, agent_loop_factory: Any | None = None) -> None:
        self._factory = agent_loop_factory
        self._capsule_prefix = "fake"

    @property
    def runner_type(self) -> str:
        return RunnerType.FAKE.value

    @property
    def health_check(self) -> RunnerHealthCheck:
        return AlwaysHealthyCheck(runner_type=RunnerType.FAKE.value)

    async def run(
        self,
        request: ExecutionRequest,
        event_callback: Any | None = None,
    ) -> ExecutionResult:
        """Execute the goal and return a structured result.

        Never raises — all errors (including asyncio.CancelledError) are
        captured in the returned ExecutionResult.
        """
        envelope = request.envelope
        capsule_id = f"{self._capsule_prefix}-{uuid.uuid4().hex[:8]}"
        t_start = time.monotonic()

        # --- Envelope integrity (G-38: use SANDBOX_VIOLATION for HMAC failure) ---
        if not verify_envelope(envelope):
            logger.warning(
                "fake_runner_hmac_failed goal_id=%s attempt=%s",
                envelope.goal_id, envelope.attempt_id,
            )
            return ExecutionResult(
                goal_id=envelope.goal_id,
                tenant_id=envelope.tenant_id,
                attempt_id=envelope.attempt_id,
                success=False,
                status="failed",
                failure_reason=ExecutionFailureReason.SANDBOX_VIOLATION,
                error_message="Envelope HMAC signature verification failed — possible tampering.",
                runner_type=self.runner_type,
                capsule_id=capsule_id,
            )

        # --- Policy gate ---
        decision = evaluate_policy(envelope)
        if not decision.allowed:
            return ExecutionResult(
                goal_id=envelope.goal_id,
                tenant_id=envelope.tenant_id,
                attempt_id=envelope.attempt_id,
                success=False,
                status="failed",
                failure_reason=decision.failure_reason,
                error_message=decision.message,
                runner_type=self.runner_type,
                capsule_id=capsule_id,
            )

        # --- Build forwarding callback with isolation metadata ---
        # Applied to ALL paths (including dry-run) for consistent SSE shape (G-37)
        forwarding_callback = None
        if event_callback is not None:
            forwarding_callback = make_forwarding_callback(
                goal_id=envelope.goal_id,
                tenant_id=envelope.tenant_id,
                downstream=event_callback,
                runner_type=self.runner_type,
                capsule_id=capsule_id,
                attempt_id=envelope.attempt_id,
            )

        # --- Dry-run fast path (mirrors GoalService dry_run=True behaviour) ---
        if envelope.dry_run:
            if forwarding_callback is not None:
                await forwarding_callback({"type": "goal_started", "goal": envelope.goal_text})
                await forwarding_callback({
                    "type": "dry_run_preview",
                    "message": "Dry run completed without executing tools or writing changes.",
                    "would_execute": False,
                })
                await forwarding_callback({"type": "goal_complete"})
            return ExecutionResult(
                goal_id=envelope.goal_id,
                tenant_id=envelope.tenant_id,
                attempt_id=envelope.attempt_id,
                success=True,
                status="complete",
                runner_type=self.runner_type,
                capsule_id=capsule_id,
                execution_time_ms=(time.monotonic() - t_start) * 1000,
            )

        # --- Build agent loop ---
        agent_loop = self._build_loop(envelope)

        # --- Build tenant context with real plan tier (G-36) ---
        from app.tenancy.context import PlanTier, TenantContext
        plan_str = str((envelope.agent_config or {}).get("plan", "professional"))
        plan_tier = PlanTier.PROFESSIONAL
        with contextlib.suppress(ValueError, TypeError):
            plan_tier = PlanTier(plan_str)

        tenant_ctx = TenantContext(
            tenant_id=envelope.tenant_id,
            plan=plan_tier,
            api_key_id="isolated-runner",
        )

        # --- Execute (G-39: catch CancelledError so "must never raise" holds) ---
        try:
            state = await agent_loop.run(
                goal=envelope.goal_text,
                tenant_ctx=tenant_ctx,
                initial_context=envelope.execution_context or None,
                event_callback=forwarding_callback,
            )
        except BaseException as exc:
            # Catches asyncio.CancelledError (inherits BaseException, not Exception)
            is_cancelled = type(exc).__name__ == "CancelledError"
            failure = (
                ExecutionFailureReason.CANCELLED
                if is_cancelled
                else ExecutionFailureReason.INTERNAL_ERROR
            )
            return ExecutionResult(
                goal_id=envelope.goal_id,
                tenant_id=envelope.tenant_id,
                attempt_id=envelope.attempt_id,
                success=False,
                status="cancelled" if is_cancelled else "failed",
                failure_reason=failure,
                error_message=str(exc),
                runner_type=self.runner_type,
                capsule_id=capsule_id,
                execution_time_ms=(time.monotonic() - t_start) * 1000,
            )

        from app.agent.state import GoalStatus
        return ExecutionResult(
            goal_id=envelope.goal_id,
            tenant_id=envelope.tenant_id,
            attempt_id=envelope.attempt_id,
            success=state.status == GoalStatus.COMPLETE,
            status=state.status.value,
            iterations=state.iterations,
            plan=state.plan,
            steps=[
                {"description": s.description, "output": s.output, "status": s.status.value}
                for s in state.steps
            ],
            verification_feedback=state.verification_feedback or "",
            runner_type=self.runner_type,
            capsule_id=capsule_id,
            execution_time_ms=(time.monotonic() - t_start) * 1000,
        )

    def _build_loop(self, envelope: Any) -> Any:
        """Return an agent runner for this envelope."""
        if self._factory is not None:
            return self._factory(envelope)

        # Default: AgentLoop with separate FakeProvider per role (G-41).
        # Each provider is given enough responses to handle up to 5 replanning
        # iterations without exhausting its response queue.
        from app.agent.loop import AgentLoop
        from app.providers.fake import FakeProvider

        _plan_resp = ['{"steps": ["Execute the goal autonomously"]}'] * 5
        _exec_resp = ["Goal executed in isolated environment"] * 5
        _verify_resp = ['{"success": true, "reason": "Completed"}'] * 5

        return AgentLoop(
            planner=FakeProvider(responses=_plan_resp),
            executor=FakeProvider(responses=_exec_resp),
            verifier=FakeProvider(responses=_verify_resp),
        )
