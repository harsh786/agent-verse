"""A minimal async Redis double for the re-embed lock/progress (KB-25).

fakeredis has no Lua support here, so the one script the lock release runs is
emulated explicitly.
"""

from __future__ import annotations

from typing import Any


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.published: list[tuple[str, str]] = []
        self.closed = False

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def set(
        self, key: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        del ex
        if nx and key in self.data:
            return None
        self.data[key] = value
        return True

    async def eval(self, script: str, numkeys: int, *args: Any) -> int:
        assert "redis.call('GET', KEYS[1]) == ARGV[1]" in script
        assert numkeys == 1
        key, owner = args
        if self.data.get(key) == owner:
            del self.data[key]
            return 1
        return 0

    async def publish(self, channel: str, message: str) -> int:
        self.published.append((channel, message))
        return 1

    async def aclose(self) -> None:
        self.closed = True
