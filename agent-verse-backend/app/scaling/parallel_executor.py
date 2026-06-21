"""Parallel step executor — run independent agent steps concurrently.

Uses asyncio.Semaphore to cap concurrency at max_concurrency, preventing
resource exhaustion when a plan has many independent steps.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class ParallelExecutor:
    """Executes async callables concurrently up to a concurrency limit.

    Args:
        max_concurrency: Maximum number of steps running simultaneously.
    """

    def __init__(self, max_concurrency: int = 8) -> None:
        self._sem = asyncio.Semaphore(max_concurrency)

    async def run_parallel(
        self, steps: list[Callable[[], Awaitable[str]]]
    ) -> list[str]:
        async def _bounded(step: Callable[[], Awaitable[str]]) -> str:
            async with self._sem:
                return await step()

        return list(await asyncio.gather(*[_bounded(s) for s in steps]))
