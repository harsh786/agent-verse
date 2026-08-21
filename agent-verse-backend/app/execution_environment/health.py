"""Health-check interface for execution-environment runners.

Each concrete runner exposes a :class:`RunnerHealthCheck` that the scheduler
uses to determine whether the runner is available before dispatching.  An
unhealthy runner + ``ISOLATED_EXECUTION_REQUIRED=true`` triggers fail-closed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class HealthStatus:
    """Result of a single health-check probe."""

    healthy: bool
    runner_type: str
    message: str = ""
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    latency_ms: float = 0.0


class RunnerHealthCheck(ABC):
    """Abstract health-check interface for a runner backend."""

    @property
    @abstractmethod
    def runner_type(self) -> str: ...

    @abstractmethod
    async def check(self) -> HealthStatus: ...


class AlwaysHealthyCheck(RunnerHealthCheck):
    """Trivially healthy check — used by the fake runner."""

    def __init__(self, runner_type: str = "fake") -> None:
        self._runner_type = runner_type

    @property
    def runner_type(self) -> str:
        return self._runner_type

    async def check(self) -> HealthStatus:
        return HealthStatus(healthy=True, runner_type=self._runner_type)


class AlwaysUnhealthyCheck(RunnerHealthCheck):
    """Trivially unhealthy check — used in tests to verify fail-closed behaviour."""

    def __init__(self, runner_type: str = "unavailable", message: str = "") -> None:
        self._runner_type = runner_type
        self._message = message or f"{runner_type} runner is not available"

    @property
    def runner_type(self) -> str:
        return self._runner_type

    async def check(self) -> HealthStatus:
        return HealthStatus(
            healthy=False,
            runner_type=self._runner_type,
            message=self._message,
        )
