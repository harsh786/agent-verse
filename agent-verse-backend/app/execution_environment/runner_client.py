"""Abstract runner interface for the execution environment.

All concrete runners (fake, local subprocess, Kubernetes) implement this
interface.  The :class:`ExecutionEnvironmentScheduler` depends only on this
interface — it never imports concrete runners directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.execution_environment.health import RunnerHealthCheck
from app.execution_environment.models import (
    CodeCancellationReceipt,
    ExecutionRequest,
    ExecutionResult,
)


class BaseRunner(ABC):
    """Contract every execution-environment runner must satisfy."""

    @property
    @abstractmethod
    def runner_type(self) -> str:
        """Machine-readable runner type name (e.g. 'fake', 'local', 'kubernetes')."""
        ...

    @property
    @abstractmethod
    def health_check(self) -> RunnerHealthCheck:
        """Return the health-check probe for this runner."""
        ...

    @abstractmethod
    async def run(
        self,
        request: ExecutionRequest,
        event_callback: Any | None = None,
    ) -> ExecutionResult:
        """Execute the goal described in ``request`` and return a structured result.

        Args:
            request: The signed execution request envelope.
            event_callback: Optional ``async def callback(event: dict) -> None``
                forwarded from the control plane.  The runner MUST call this for
                every agent-loop event so the SSE stream stays live.

        Returns:
            An :class:`ExecutionResult` with ``success``, ``status``, and any
            isolation metadata.  Must never raise — all errors must be captured
            in the ``ExecutionResult``.
        """
        ...

    @abstractmethod
    async def cancel(self, workload_id: str, reason: str) -> CodeCancellationReceipt:
        """Terminate a workload and return an idempotent cleanup receipt."""
        ...
