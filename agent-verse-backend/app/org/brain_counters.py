from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

_TTL = 48 * 3600


class BrainCounters:
    def __init__(self, redis: Any, org_id: str) -> None:
        self._r = redis
        self._org = org_id

    def _day(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d")

    def _k(self, suffix: str) -> str:
        return f"orgbrain:{self._org}:{self._day()}:{suffix}"

    async def snapshot(self) -> tuple[float, int, float]:
        spend = await self._r.get(self._k("spend"))
        count = await self._r.get(self._k("count"))
        last = await self._r.get(f"orgbrain:{self._org}:last_launch")
        since = (time.time() - float(last)) if last else 1e9
        return (float(spend or 0.0), int(count or 0), since)

    async def record_launch(self, est_cost_usd: float) -> None:
        await self._r.incrbyfloat(self._k("spend"), float(est_cost_usd))
        await self._r.expire(self._k("spend"), _TTL)
        await self._r.incr(self._k("count"))
        await self._r.expire(self._k("count"), _TTL)
        await self._r.set(f"orgbrain:{self._org}:last_launch", str(time.time()), ex=_TTL)

    async def acquire_tick_lock(self, ttl_seconds: int = 120) -> bool:
        got = await self._r.set(f"orgbrain:{self._org}:tick_lock", "1", nx=True, ex=ttl_seconds)
        return bool(got)
