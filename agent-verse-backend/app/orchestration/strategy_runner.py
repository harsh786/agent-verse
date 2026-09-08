"""Bounded, idempotent lifecycle runner for version-pinned strategies."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.orchestration.strategy_contracts import (
    ExecutionTerminalState,
    PatternLimits,
    SafeTraceSummary,
    StrategyCheckpoint,
    StrategyExecutionRequest,
    StrategyExecutionResult,
)
from app.orchestration.strategy_registry import StrategyRegistry


@dataclass(frozen=True, slots=True)
class ExecutionMetrics:
    calls: int = 0
    nodes: int = 0
    edges: int = 0
    depth: int = 0
    fan_out: int = 0
    rounds: int = 0
    tokens: int = 0
    duration_seconds: float = 0.0
    cost_usd: float = 0.0


@dataclass(frozen=True, slots=True)
class StrategyRunOutput:
    answer: str | None = None
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)
    checkpoint_cursor: str | None = None
    next_action: str | None = None
    safe_rationale_summary: str = "Strategy execution completed."


Executor = Callable[
    [StrategyExecutionRequest, Any, asyncio.Event, StrategyCheckpoint | None],
    StrategyRunOutput | Awaitable[StrategyRunOutput],
]
Admission = Callable[[StrategyExecutionRequest], tuple[bool, str]]
CheckpointCallback = Callable[[StrategyCheckpoint], Any]
ReserveBudget = Callable[[StrategyExecutionRequest, PatternLimits], bool]
ReleaseBudget = Callable[[StrategyExecutionRequest], Any]


async def _default_executor(
    request: StrategyExecutionRequest,
    runtime: Any,
    cancelled: asyncio.Event,
    checkpoint: StrategyCheckpoint | None,
) -> StrategyRunOutput:
    del request, runtime, cancelled, checkpoint
    raise RuntimeError("strategy executor is not configured")


class StrategyRunner:
    def __init__(
        self,
        registry: StrategyRegistry,
        *,
        executor: Executor = _default_executor,
        admission: Admission = lambda _: (True, "admitted"),
        checkpoint_callback: CheckpointCallback | None = None,
        reserve_budget: ReserveBudget = lambda _request, _limits: True,
        release_budget: ReleaseBudget = lambda _request: None,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._admission = admission
        self._checkpoint_callback = checkpoint_callback
        self._reserve_budget = reserve_budget
        self._release_budget = release_budget
        self._cancel_events: dict[str, asyncio.Event] = {}
        self._results: dict[tuple[str, str], StrategyExecutionResult] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    @property
    def has_real_executor(self) -> bool:
        """False while this runner still carries the inert module-default executor.

        D-1: a ``StrategyRunner`` built with no ``executor=`` override always fails every
        execution with ``RuntimeError("strategy executor is not configured")``. Callers (e.g.
        ``GoalService``) use this to decide whether to dispatch through the runner at all or
        fall back to the local AgentGraph kernel.
        """
        return self._executor is not _default_executor

    def cancel(self, cancellation_token: str) -> None:
        self._cancel_events.setdefault(cancellation_token, asyncio.Event()).set()

    @staticmethod
    async def _invoke(callback: Callable[..., Any], *args: Any) -> Any:
        result = callback(*args)
        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    def _result(
        terminal_state: ExecutionTerminalState,
        *,
        answer: str | None = None,
        reason_codes: tuple[str, ...] = (),
        limit_type: str | None = None,
        metrics: ExecutionMetrics | None = None,
        next_action: str | None = None,
        rationale: str = "Strategy execution stopped safely.",
    ) -> StrategyExecutionResult:
        measured = metrics or ExecutionMetrics()
        counts = {
            key: value
            for key, value in {
                "calls": measured.calls,
                "nodes": measured.nodes,
                "edges": measured.edges,
                "depth": measured.depth,
                "fan_out": measured.fan_out,
                "rounds": measured.rounds,
                "tokens": measured.tokens,
            }.items()
            if value
        }
        return StrategyExecutionResult(
            terminal_state=terminal_state,
            answer=answer,
            evidence=(),
            artifacts=(),
            cost_usd=measured.cost_usd,
            next_action=next_action,
            safe_rationale_summary=rationale[:500] or "Strategy execution stopped safely.",
            trace_summary=SafeTraceSummary(
                phase="terminal",
                status=terminal_state.value,
                terminal_state=terminal_state,
                reason_codes=reason_codes,
                limit_type=limit_type,
                counts=counts,
                cost_usd=measured.cost_usd,
                duration_seconds=measured.duration_seconds,
            ),
        )

    @staticmethod
    def _exceeded_limit(
        metrics: ExecutionMetrics,
        limits: PatternLimits,
    ) -> str | None:
        for field_name in (
            "calls",
            "nodes",
            "edges",
            "depth",
            "fan_out",
            "rounds",
            "tokens",
            "duration_seconds",
            "cost_usd",
        ):
            if getattr(metrics, field_name) > getattr(limits, field_name):
                return field_name
        return None

    async def run(
        self,
        request: StrategyExecutionRequest,
        limits: PatternLimits,
        *,
        checkpoint: StrategyCheckpoint | None = None,
    ) -> StrategyExecutionResult:
        cache_key = (request.tenant_id, request.idempotency_key)
        lock = self._locks.setdefault(cache_key, asyncio.Lock())
        async with lock:
            cached = self._results.get(cache_key)
            if cached is not None:
                return cached
            result = await self._run_once(request, limits, checkpoint=checkpoint)
            self._results[cache_key] = result
            return result

    async def _run_once(
        self,
        request: StrategyExecutionRequest,
        limits: PatternLimits,
        *,
        checkpoint: StrategyCheckpoint | None,
    ) -> StrategyExecutionResult:
        cancelled = self._cancel_events.setdefault(request.cancellation_token, asyncio.Event())
        if cancelled.is_set():
            return self._result(ExecutionTerminalState.CANCELLED)
        if request.deadline <= datetime.now(UTC):
            return self._result(ExecutionTerminalState.DEADLINE_EXCEEDED)

        try:
            capability = self._registry.resolve(request.strategy_id).capability
            descriptor = self._registry.resolve_adapter(request.strategy_id)
        except LookupError:
            return self._result(
                ExecutionTerminalState.FAILED,
                reason_codes=("adapter_not_found",),
            )
        if (
            request.adapter_version != capability.adapter_version
            or request.state_schema_version != capability.state_schema_version
        ):
            return self._result(
                ExecutionTerminalState.FAILED,
                reason_codes=("adapter_version_mismatch",),
            )
        if checkpoint is not None and (
            checkpoint.strategy_id != capability.strategy_id
            or checkpoint.adapter_version != request.adapter_version
            or checkpoint.state_schema_version != request.state_schema_version
        ):
            return self._result(
                ExecutionTerminalState.FAILED,
                reason_codes=("resume_blocked",),
            )

        admitted, admission_reason = self._admission(request)
        if not admitted:
            return self._result(
                ExecutionTerminalState.POLICY_DENIED,
                reason_codes=(admission_reason,),
            )
        reserved = self._reserve_budget(request, limits)
        if not reserved:
            return self._result(
                ExecutionTerminalState.POLICY_DENIED,
                reason_codes=("budget_reservation_failed",),
            )

        try:
            adapter = descriptor.create_adapter()
            runtime = adapter.create_runtime
            execution = asyncio.create_task(
                self._invoke(self._executor, request, runtime, cancelled, checkpoint)
            )
            cancellation = asyncio.create_task(cancelled.wait())
            timeout = min(
                limits.duration_seconds,
                max(0.0, (request.deadline - datetime.now(UTC)).total_seconds()),
            )
            done, _ = await asyncio.wait(
                {execution, cancellation},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancellation in done and cancelled.is_set():
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                return self._result(ExecutionTerminalState.CANCELLED)
            cancellation.cancel()
            await asyncio.gather(cancellation, return_exceptions=True)
            if execution not in done:
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                terminal = (
                    ExecutionTerminalState.DEADLINE_EXCEEDED
                    if request.deadline <= datetime.now(UTC)
                    else ExecutionTerminalState.LIMIT_EXCEEDED
                )
                return self._result(
                    terminal,
                    limit_type=(
                        "duration_seconds"
                        if terminal is ExecutionTerminalState.LIMIT_EXCEEDED
                        else None
                    ),
                )
            output = execution.result()
            exceeded = self._exceeded_limit(output.metrics, limits)
            if exceeded is not None:
                return self._result(
                    ExecutionTerminalState.LIMIT_EXCEEDED,
                    limit_type=exceeded,
                    metrics=output.metrics,
                )
            if output.checkpoint_cursor and self._checkpoint_callback is not None:
                saved = StrategyCheckpoint(
                    strategy_id=capability.strategy_id,
                    adapter_version=capability.adapter_version,
                    state_schema_version=capability.state_schema_version,
                    cursor=output.checkpoint_cursor,
                    state_ref=f"{request.runtime_profile_ref}:{output.checkpoint_cursor}",
                    created_at=datetime.now(UTC),
                )
                await self._invoke(self._checkpoint_callback, saved)
            return self._result(
                ExecutionTerminalState.SUCCEEDED,
                answer=output.answer,
                metrics=output.metrics,
                next_action=output.next_action,
                rationale=output.safe_rationale_summary,
            )
        except Exception:
            return self._result(
                ExecutionTerminalState.FAILED,
                reason_codes=("execution_failed",),
            )
        finally:
            await self._invoke(self._release_budget, request)
