"""Startup progress tracking: serve fast, report readiness honestly.

The API lifespan used to do every warm-up inline, so uvicorn only bound its
socket after minutes of per-tenant loops. Work that is not needed to answer a
request correctly (warm caches, catalogue seeding, memory hydration) now runs as
tracked background tasks started from the lifespan; ``GET /health/ready`` reports
not-ready until every *gating* task has finished, while ``GET /health`` stays a
fast dependency check.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class _TaskRecord:
    name: str
    gating: bool
    started_at: float
    status: str = "running"  # running | done | failed
    finished_at: float | None = None
    task: asyncio.Task[Any] | None = None


@dataclass
class StartupTracker:
    """Tracks the lifespan's background warm-up tasks and essential phase."""

    started_at: float = field(default_factory=time.monotonic)
    essential_done: bool = False
    serving_at: float | None = None
    _tasks: dict[str, _TaskRecord] = field(default_factory=dict)

    def mark_essential_done(self) -> None:
        """Essential state is loaded; the lifespan is about to yield (serve)."""
        self.essential_done = True
        self.serving_at = time.monotonic()
        logger.info(
            "startup_serving",
            startup_seconds=round(self.serving_at - self.started_at, 3),
            background_pending=self.pending(),
        )

    def spawn(
        self,
        name: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        gating: bool = True,
    ) -> asyncio.Task[Any]:
        """Run ``factory()`` in the background, recording its outcome.

        ``gating`` tasks hold readiness until they finish. A failed task is
        logged and still counts as finished: readiness must not stay down
        forever because an optional warm-up (e.g. catalogue seeding) failed.
        """
        record = _TaskRecord(name=name, gating=gating, started_at=time.monotonic())

        async def _run() -> Any:
            try:
                result = await factory()
            except asyncio.CancelledError:
                record.status = "failed"
                raise
            except Exception as exc:
                record.status = "failed"
                logger.warning("startup_task_failed", task=name, error=str(exc))
                return None
            else:
                record.status = "done"
                return result
            finally:
                record.finished_at = time.monotonic()
                logger.info(
                    "startup_task_finished",
                    task=name,
                    status=record.status,
                    seconds=round(record.finished_at - record.started_at, 3),
                )

        task = asyncio.create_task(_run(), name=f"startup:{name}")
        record.task = task
        self._tasks[name] = record
        return task

    def pending(self, *, gating_only: bool = True) -> list[str]:
        return sorted(
            r.name
            for r in self._tasks.values()
            if r.status == "running" and (r.gating or not gating_only)
        )

    @property
    def ready(self) -> bool:
        return self.essential_done and not self.pending()

    def snapshot(self) -> dict[str, Any]:
        return {
            "essential_done": self.essential_done,
            "startup_seconds": (
                round(self.serving_at - self.started_at, 3) if self.serving_at else None
            ),
            "tasks": {
                r.name: {"status": r.status, "gating": r.gating} for r in self._tasks.values()
            },
            "pending": self.pending(),
        }

    async def wait_ready(self, timeout: float | None = None) -> bool:
        """Await every gating task (tests / tooling). Returns readiness."""
        tasks = [r.task for r in self._tasks.values() if r.gating and r.task is not None]
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)
        return self.ready

    async def cancel_all(self) -> None:
        """Shutdown: cancel background tasks still running and await them."""
        running = [r.task for r in self._tasks.values() if r.task is not None and not r.task.done()]
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
