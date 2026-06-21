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


@dataclass(frozen=True, slots=True)
class HealthCheck:
    name: str
    check: Callable[[], Awaitable[None]]


@dataclass(slots=True)
class HealthRegistry:
    checks: list[HealthCheck] = field(default_factory=list)

    def register(self, check: HealthCheck) -> None:
        self.checks.append(check)

    async def run(self) -> tuple[bool, dict[str, Any]]:
        """Run every check concurrently; return (all_healthy, per-check results)."""
        if not self.checks:
            return True, {}

        async def _run_one(hc: HealthCheck) -> tuple[str, dict[str, Any]]:
            try:
                await hc.check()
                return hc.name, {"status": "up"}
            except Exception as exc:  # surface any failure as "down"
                return hc.name, {"status": "down", "error": str(exc)}

        results = await asyncio.gather(*(_run_one(hc) for hc in self.checks))
        report = dict(results)
        healthy = all(item["status"] == "up" for item in report.values())
        return healthy, report
