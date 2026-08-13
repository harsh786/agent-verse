"""Governed internal boundary for code-interpreter workloads."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from app.execution_environment.code_validation import CodeWorkloadValidator
from app.execution_environment.envelope import build_envelope
from app.execution_environment.models import (
    CodeExecutionObservation,
    CodeExecutionWorkload,
    ExecutionKind,
    ExecutionResult,
)
from app.execution_environment.observation_sanitizer import sanitize_observation


class GovernedToolInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(min_length=1)
    goal_id: str = Field(min_length=1)
    strategy_execution_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    deadline: datetime
    authenticated: bool = True
    authorized: bool = True
    feature_enabled: bool = True
    classification_allowed: bool = True
    approval_state: str = "approved"
    budget_available: bool = True
    cancelled: bool = False
    correlation_id: str = Field(min_length=1)
    causation_id: str = Field(min_length=1)


class CodeInterpreterDeniedError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CodeInterpreterTool:
    name: Final[str] = "code_interpreter.execute"

    def __init__(
        self,
        *,
        scheduler: Any,
        audit: Callable[[str, dict[str, Any]], Any],
    ) -> None:
        self._scheduler = scheduler
        self._audit = audit
        self._validator = CodeWorkloadValidator()
        self._results: dict[str, CodeExecutionObservation] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @staticmethod
    async def _invoke(callback: Any, *args: Any, **kwargs: Any) -> Any:
        result = callback(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    async def _record(
        self, event: str, invocation: GovernedToolInvocation, workload: CodeExecutionWorkload
    ) -> None:
        await self._invoke(
            self._audit,
            event,
            {
                "tenant_id": invocation.tenant_id,
                "goal_id": invocation.goal_id,
                "strategy_id": invocation.strategy_id,
                "strategy_execution_id": invocation.strategy_execution_id,
                "workload_id": workload.workload_id,
                "source_sha256": workload.source_sha256,
                "policy_version": invocation.policy_version,
                "correlation_id": invocation.correlation_id,
                "causation_id": invocation.causation_id,
            },
        )

    async def execute(
        self,
        *,
        invocation: GovernedToolInvocation,
        workload: CodeExecutionWorkload,
    ) -> CodeExecutionObservation:
        denial = next(
            (
                code
                for condition, code in (
                    (not invocation.authenticated, "unauthenticated"),
                    (not invocation.authorized, "unauthorized"),
                    (not invocation.feature_enabled, "feature_disabled"),
                    (not invocation.classification_allowed, "classification_denied"),
                    (
                        invocation.approval_state != "approved",
                        f"approval_{invocation.approval_state}",
                    ),
                    (not invocation.budget_available, "budget_exhausted"),
                    (invocation.cancelled, "cancelled"),
                    (invocation.deadline <= datetime.now(UTC), "deadline_exceeded"),
                )
                if condition
            ),
            None,
        )
        violations = self._validator.validate(workload)
        if denial is None and violations:
            denial = violations[0].code
        if denial is not None:
            await self._record("code.denied", invocation, workload)
            raise CodeInterpreterDeniedError(denial)

        key = (
            f"{invocation.strategy_execution_id}:{workload.workload_id}:"
            f"{workload.source_sha256}"
        )
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if key in self._results:
                return self._results[key]
            await self._record("code.requested", invocation, workload)
            envelope = build_envelope(
                tenant_id=invocation.tenant_id,
                goal_id=invocation.goal_id,
                execution_kind=ExecutionKind.CODE_INTERPRETER,
                code_workload=workload,
            )
            try:
                result = await self._invoke(self._scheduler.schedule, envelope)
            except Exception:
                await self._record("code.failed", invocation, workload)
                raise
            observation = self._to_observation(workload, result)
            self._results[key] = observation
            await self._record(
                "code.completed" if observation.terminal_state == "completed" else "code.failed",
                invocation,
                workload,
            )
            return observation

    @staticmethod
    def _to_observation(
        workload: CodeExecutionWorkload, result: CodeExecutionObservation | ExecutionResult
    ) -> CodeExecutionObservation:
        if isinstance(result, CodeExecutionObservation):
            stdout = sanitize_observation(result.stdout)
            stderr = sanitize_observation(result.stderr, maximum_bytes=32 * 1024)
            payload = result.model_dump(exclude={"observation_sha256"}, mode="json")
            payload.update(
                stdout=stdout.text,
                stderr=stderr.text,
                stdout_truncated=result.stdout_truncated or stdout.truncated,
                stderr_truncated=result.stderr_truncated or stderr.truncated,
            )
        else:
            if result.code_observation is not None:
                return CodeInterpreterTool._to_observation(
                    workload, result.code_observation
                )
            terminal = "completed" if result.success else "failed"
            payload = {
                "workload_id": workload.workload_id,
                "source_sha256": workload.source_sha256,
                "exit_code": result.exit_code,
                "terminal_state": terminal,
                "stdout": "",
                "stderr": sanitize_observation(result.error_message).text,
                "stdout_truncated": False,
                "stderr_truncated": False,
                "result_json": None,
                "artifact_refs": tuple(item.storage_url for item in result.artifacts),
                "cpu_time_ms": 0,
                "wall_time_ms": max(0, int(result.execution_time_ms)),
                "peak_memory_bytes": 0,
                "denial_codes": (),
            }
        digest = hashlib.sha256(
            repr(sorted(payload.items(), key=lambda item: item[0])).encode()
        ).hexdigest()
        return CodeExecutionObservation.model_validate(
            {**payload, "observation_sha256": digest}
        )


CodeInterpreterDenied = CodeInterpreterDeniedError


__all__ = [
    "CodeInterpreterDenied",
    "CodeInterpreterDeniedError",
    "CodeInterpreterTool",
    "GovernedToolInvocation",
]
