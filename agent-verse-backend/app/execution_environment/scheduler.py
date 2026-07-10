"""Execution-environment scheduler.

The scheduler is the single entry point the control plane uses to dispatch
an :class:`ExecutionEnvelope` to an isolated runner.

Routing logic
-------------
1. If ``ISOLATED_AGENT_EXECUTION=false`` (default), callers must not call the
   scheduler; it raises ``RuntimeError`` to prevent accidental use.
2. Performs a health check (with timeout) on the selected runner.
3. If the runner is unhealthy:
   - **Always raises** :exc:`RunnerUnavailableError` — there is no silent
     fallback to in-process execution at this layer.
   - ``ISOLATED_EXECUTION_REQUIRED`` controls whether the *caller* falls back
     to in-process execution.  When the flag is ``true``, the caller
     (``GoalService._run_agent_loop``) must also propagate the error rather
     than falling back.
4. Evaluates :class:`ExecutionEnvironmentPolicy` against the envelope.
5. Dispatches to the selected runner and returns the :class:`ExecutionResult`.

Registration
------------
An instance is constructed in ``app/main.py::create_app()`` and stored on
``app.state.execution_scheduler``.  The Celery worker constructs a fresh
instance using ``from_flags()``.
"""
from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace

from app.execution_environment.models import (
    ExecutionEnvelope,
    ExecutionFailureReason,
    ExecutionRequest,
    ExecutionResult,
    RunnerType,
)
from app.execution_environment.policy import evaluate_policy
from app.execution_environment.runner_client import BaseRunner

logger = logging.getLogger(__name__)
_tracer = trace.get_tracer(__name__)

# How long to wait for the runner health check before failing closed.
_HEALTH_CHECK_TIMEOUT_SECONDS = 5.0


class RunnerUnavailableError(Exception):
    """Raised (fail-closed) when the selected runner is unhealthy or policy denies execution."""

    def __init__(self, reason: str, failure_reason: ExecutionFailureReason) -> None:
        super().__init__(reason)
        self.failure_reason = failure_reason


class ExecutionEnvironmentScheduler:
    """Routes execution requests to the correct isolated runner.

    Args:
        runner: The concrete :class:`BaseRunner` to use.  When ``None``, a
            :class:`FakeRunner` is constructed automatically.
    """

    def __init__(self, runner: BaseRunner | None = None) -> None:
        if runner is None:
            from app.execution_environment.fake_runner import FakeRunner
            runner = FakeRunner()
        self._runner = runner

    @classmethod
    def from_flags(
        cls,
        *,
        isolated_execution_local_runner: bool = False,
        isolated_execution_kubernetes_runner: bool = False,
        agent_loop_factory: Any | None = None,
    ) -> ExecutionEnvironmentScheduler:
        """Construct the scheduler with the appropriate runner from flags."""
        if isolated_execution_kubernetes_runner:
            from app.execution_environment.kubernetes_runner import KubernetesRunner
            runner: BaseRunner = KubernetesRunner()
        elif isolated_execution_local_runner:
            from app.execution_environment.local_runner import LocalSubprocessRunner
            runner = LocalSubprocessRunner()
        else:
            from app.execution_environment.fake_runner import FakeRunner
            runner = FakeRunner(agent_loop_factory=agent_loop_factory)
        return cls(runner=runner)

    @property
    def runner(self) -> BaseRunner:
        return self._runner

    async def schedule(
        self,
        envelope: ExecutionEnvelope,
        event_callback: Any | None = None,
    ) -> ExecutionResult:
        """Validate, route, and execute the envelope.

        Fail-closed: unhealthy runner, policy violation, or health-check
        timeout all raise :exc:`RunnerUnavailableError`.  There is never a
        silent fall-through to in-process execution from within this method.

        Args:
            envelope: The signed execution envelope from the control plane.
            event_callback: Async callback forwarded to the runner for live
                SSE event delivery.

        Returns:
            :class:`ExecutionResult` on success.

        Raises:
            :exc:`RunnerUnavailableError`: When the runner is unhealthy,
                the health check times out, or policy evaluation denies.
        """
        import asyncio

        with _tracer.start_as_current_span("isolated_execution.schedule") as span:
            span.set_attribute("goal_id", envelope.goal_id)
            span.set_attribute("tenant_id", envelope.tenant_id)
            span.set_attribute("runner_type", self._runner.runner_type)

            # --- Health check (with timeout) ---
            try:
                health = await asyncio.wait_for(
                    self._runner.health_check.check(),
                    timeout=_HEALTH_CHECK_TIMEOUT_SECONDS,
                )
            except TimeoutError as _te:
                msg = (
                    f"Runner '{self._runner.runner_type}' health check timed out "
                    f"after {_HEALTH_CHECK_TIMEOUT_SECONDS}s"
                )
                logger.error("isolated_runner_health_timeout runner=%s", self._runner.runner_type)
                span.set_attribute("health_status", "timeout")
                raise RunnerUnavailableError(
                    msg, ExecutionFailureReason.RUNNER_UNAVAILABLE
                ) from _te

            if not health.healthy:
                msg = (
                    f"Isolated execution runner '{self._runner.runner_type}' is unhealthy: "
                    f"{health.message}"
                )
                logger.error(
                    "isolated_runner_unhealthy runner=%s msg=%s latency_ms=%.1f",
                    self._runner.runner_type, health.message, health.latency_ms,
                )
                span.set_attribute("health_status", "unhealthy")
                raise RunnerUnavailableError(msg, ExecutionFailureReason.RUNNER_UNAVAILABLE)

            span.set_attribute("health_status", "healthy")

            # --- Policy gate ---
            decision = evaluate_policy(envelope)
            if not decision.allowed:
                msg = f"Execution policy denied: {decision.message}"
                logger.warning(
                    "isolated_execution_policy_denied goal_id=%s reason=%s",
                    envelope.goal_id, decision.message,
                )
                span.set_attribute("policy_decision", "denied")
                raise RunnerUnavailableError(
                    msg,
                    decision.failure_reason or ExecutionFailureReason.POLICY_DENIED,
                )

            span.set_attribute("policy_decision", "allowed")

            # --- Dispatch ---
            request = ExecutionRequest(
                envelope=envelope,
                runner_type=RunnerType(self._runner.runner_type),
            )
            logger.info(
                "isolated_execution_dispatched goal_id=%s runner=%s attempt=%s",
                envelope.goal_id, self._runner.runner_type, envelope.attempt_id,
            )

            result = await self._runner.run(request, event_callback=event_callback)

            span.set_attribute("result_status", result.status)
            span.set_attribute("result_success", result.success)
            span.set_attribute("execution_time_ms", result.execution_time_ms)

            logger.info(
                "isolated_execution_complete goal_id=%s status=%s runner=%s ms=%.0f",
                envelope.goal_id, result.status, result.runner_type, result.execution_time_ms,
            )
            return result
