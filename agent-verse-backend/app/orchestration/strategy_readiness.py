"""Bounded, fail-closed operational readiness evaluation for strategies."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.orchestration.strategy_registry import StrategyCapability, StrategyState


@dataclass(frozen=True, slots=True)
class DependencyProbeResult:
    available: bool
    reason: str = "ready"
    is_degraded: bool = False

    @classmethod
    def ready(cls) -> DependencyProbeResult:
        return cls(True)

    @classmethod
    def not_ready(cls, reason: str) -> DependencyProbeResult:
        return cls(False, reason)

    @classmethod
    def degraded(cls, reason: str) -> DependencyProbeResult:
        return cls(True, reason, True)


@dataclass(frozen=True, slots=True)
class ReadinessDecision:
    ready: bool
    blocking_reasons: tuple[str, ...]
    degraded_reasons: tuple[str, ...]
    checked_at: datetime


Probe = Callable[[], DependencyProbeResult | Awaitable[DependencyProbeResult]]


class ReadinessEvaluator:
    def __init__(
        self,
        *,
        probe_timeout: float = 1.0,
        cache_ttl: timedelta = timedelta(seconds=30),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if probe_timeout <= 0:
            raise ValueError("probe_timeout must be positive")
        if cache_ttl.total_seconds() < 0:
            raise ValueError("cache_ttl cannot be negative")
        self._probe_timeout = probe_timeout
        self._cache_ttl = cache_ttl
        self._clock = clock
        self._probes: dict[str, Probe] = {}
        self._cache: dict[str, tuple[datetime, DependencyProbeResult]] = {}

    def register(self, dependency_id: str, probe: Probe) -> None:
        if not dependency_id or dependency_id in self._probes:
            raise ValueError(f"invalid or duplicate dependency probe: {dependency_id}")
        self._probes[dependency_id] = probe

    async def _run_probe(self, dependency_id: str) -> DependencyProbeResult:
        now = self._clock()
        cached = self._cache.get(dependency_id)
        if cached is not None and now - cached[0] <= self._cache_ttl:
            return cached[1]
        probe = self._probes[dependency_id]
        try:
            value = probe()
            if inspect.isawaitable(value):
                result = await asyncio.wait_for(value, timeout=self._probe_timeout)
            else:
                result = value
            if not isinstance(result, DependencyProbeResult):
                result = DependencyProbeResult.not_ready("invalid_probe_result")
        except TimeoutError:
            result = DependencyProbeResult.not_ready("probe_timeout")
        except Exception:
            result = DependencyProbeResult.not_ready("probe_error")
        self._cache[dependency_id] = (now, result)
        return result

    async def evaluate(
        self,
        capability: StrategyCapability,
        *,
        optional_dependencies: tuple[str, ...] = (),
        production: bool = True,
    ) -> ReadinessDecision:
        now = self._clock()
        if capability.state is StrategyState.DISABLED:
            return ReadinessDecision(False, ("strategy_disabled",), (), now)

        blocking: list[str] = []
        degraded: list[str] = []
        for dependency_id in capability.readiness_requirements:
            if dependency_id == "registry_contract":
                continue
            if dependency_id not in self._probes:
                if production:
                    blocking.append(f"missing_probe:{dependency_id}")
                continue
            result = await self._run_probe(dependency_id)
            if not result.available:
                blocking.append(f"{dependency_id}:{result.reason}")
            elif result.is_degraded:
                degraded.append(f"{dependency_id}:{result.reason}")

        for dependency_id in optional_dependencies:
            if dependency_id not in self._probes:
                degraded.append(f"missing_optional_probe:{dependency_id}")
                continue
            result = await self._run_probe(dependency_id)
            if not result.available or result.is_degraded:
                degraded.append(f"{dependency_id}:{result.reason}")

        return ReadinessDecision(not blocking, tuple(blocking), tuple(degraded), now)

