"""In-process Redis stand-in implementing RedisLeaseLimiter's Lua scripts plus the
string commands the RPA session registry uses (fakeredis has no EVAL)."""

from __future__ import annotations

import time
from typing import Any


class LeaseRedis:
    def __init__(self) -> None:
        self.strings: dict[str, tuple[str, float]] = {}  # key -> (value, expires_at)
        self.zsets: dict[str, dict[str, float]] = {}  # key -> member -> expiry ms
        self.fail = False

    def _now_ms(self) -> float:
        return time.time() * 1000

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis down")

    # ── strings ──────────────────────────────────────────────────────────────
    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._check()
        self.strings[key] = (value, time.time() + ttl)

    async def set(self, key: str, value: str, ex: int | None = None, **_kw: Any) -> bool:
        self._check()
        self.strings[key] = (value, time.time() + (ex or 10**9))
        return True

    async def get(self, key: str) -> str | None:
        self._check()
        item = self.strings.get(key)
        if item is None or item[1] <= time.time():
            self.strings.pop(key, None)
            return None
        return item[0]

    async def expire(self, key: str, ttl: int) -> bool:
        self._check()
        if key in self.strings:
            self.strings[key] = (self.strings[key][0], time.time() + ttl)
            return True
        return False

    async def delete(self, *keys: str) -> int:
        self._check()
        return sum(1 for k in keys if self.strings.pop(k, None) is not None)

    async def keys(self, pattern: str) -> list[str]:
        prefix = pattern.rstrip("*")
        return [k for k in self.strings if k.startswith(prefix)]

    def ttl_left(self, key: str) -> float:
        return self.strings[key][1] - time.time()

    # ── lease sorted sets (RedisLeaseLimiter) ────────────────────────────────
    # Redis EVAL (server-side Lua) emulation, not Python eval.
    async def eval(self, script: str, _n: int, key: str, *args: str) -> Any:
        self._check()
        now = self._now_ms()
        zs = self.zsets.setdefault(key, {})
        for m in [m for m, exp in zs.items() if exp <= now]:
            del zs[m]
        if "ZCARD" in script:  # acquire
            limit, lease_ms, member = int(args[0]), int(args[1]), args[2]
            if member in zs:
                zs[member] = now + lease_ms
                return 1
            if len(zs) >= limit:
                return 0
            zs[member] = now + lease_ms
            return 1
        if "ZRANGE" in script:  # members
            return list(zs)
        lease_ms, member = int(args[0]), args[1]  # refresh
        if member not in zs:
            return 0
        zs[member] = now + lease_ms
        return 1

    async def zrem(self, key: str, member: str) -> int:
        self._check()
        return 1 if self.zsets.get(key, {}).pop(member, None) is not None else 0
