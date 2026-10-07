"""A Redis blip must not turn scope resolution into a 500 (live baseline finding)."""

from __future__ import annotations

from typing import Any

import pytest

from app.auth.permission_cache import PermissionCache


class _DownRedis:
    async def get(self, *_a: Any) -> Any:
        raise ConnectionRefusedError(111, "Connection refused")

    async def setex(self, *_a: Any) -> Any:
        raise ConnectionRefusedError(111, "Connection refused")

    async def delete(self, *_a: Any) -> Any:
        raise ConnectionRefusedError(111, "Connection refused")

    async def scan(self, *_a: Any, **_k: Any) -> Any:
        raise ConnectionRefusedError(111, "Connection refused")


async def test_read_failure_is_a_cache_miss() -> None:
    assert await PermissionCache(_DownRedis()).get("t1", "k1") is None


async def test_write_failure_is_skipped() -> None:
    await PermissionCache(_DownRedis()).set("t1", "k1", {"goals:read"})
    await PermissionCache(_DownRedis()).set("t1", "k1", set())


async def test_invalidation_failure_still_raises() -> None:
    with pytest.raises(ConnectionRefusedError):
        await PermissionCache(_DownRedis()).invalidate_tenant("t1")


async def test_corrupt_entry_is_a_miss() -> None:
    class _Corrupt(_DownRedis):
        async def get(self, *_a: Any) -> Any:
            return b"not-json"

    assert await PermissionCache(_Corrupt()).get("t1", "k1") is None
