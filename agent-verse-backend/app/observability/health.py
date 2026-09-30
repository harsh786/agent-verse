"""Composable health checks.

A :class:`HealthCheck` wraps an async callable that raises on failure. The
:class:`HealthRegistry` runs all registered checks concurrently and aggregates them into
an overall status. Phases that own a dependency (Postgres, Redis, MCP) register their own
check, so ``/health`` extends without modification.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class HealthCheck:
    name: str
    check: Callable[[], Awaitable[None]]


@dataclass(slots=True)
class HealthRegistry:
    checks: list[HealthCheck] = field(default_factory=list)
    # Each check is bounded: /health is the liveness probe, and one dependency
    # awaiting a dead connection (e.g. Redis OOM-killed under a live process)
    # used to hang every probe instead of reporting that dependency down.
    check_timeout_s: float = 5.0

    def register(self, check: HealthCheck) -> None:
        self.checks.append(check)

    async def run(self) -> tuple[bool, dict[str, Any]]:
        """Run every check concurrently; return (all_healthy, per-check results)."""
        if not self.checks:
            return True, {}

        async def _run_one(hc: HealthCheck) -> tuple[str, dict[str, Any]]:
            try:
                await asyncio.wait_for(hc.check(), timeout=self.check_timeout_s)
                return hc.name, {"status": "up"}
            except TimeoutError:
                logger.warning(
                    "health_check_timed_out", check=hc.name, timeout_s=self.check_timeout_s
                )
                return hc.name, {"status": "down", "error": "timed out"}
            except Exception as exc:  # surface any failure as "down"
                # The report is served by the unauthenticated GET /health. It used
                # to carry ``str(exc)``, leaking DSNs (with credentials), internal
                # hostnames and driver errors to anyone. Log the detail; return a
                # generic marker (the "error" key is kept for response-shape compat).
                logger.warning(
                    "health_check_failed",
                    check=hc.name,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                return hc.name, {"status": "down", "error": "check failed"}

        results = await asyncio.gather(*(_run_one(hc) for hc in self.checks))
        report = dict(results)
        healthy = all(item["status"] == "up" for item in report.values())
        return healthy, report
