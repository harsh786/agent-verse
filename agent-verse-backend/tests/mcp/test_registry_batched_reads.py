"""MCPREG-06: listing connectors is one MGET round-trip, not a GET per connector."""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest

from app.mcp.registry import MCPRegistry, MCPServerConfig
from app.tenancy.context import PlanTier, TenantContext

_CTX = TenantContext(tenant_id="t-batch", plan=PlanTier.FREE, api_key_id="k")


class _Counting:
    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.gets = 0
        self.mgets = 0

    async def get(self, *a: Any, **k: Any) -> Any:
        self.gets += 1
        return await self._inner.get(*a, **k)

    async def mget(self, *a: Any, **k: Any) -> Any:
        self.mgets += 1
        return await self._inner.mget(*a, **k)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


@pytest.mark.asyncio
async def test_list_is_one_mget() -> None:
    redis = _Counting(fakeredis.aioredis.FakeRedis(decode_responses=True))
    reg = MCPRegistry(redis)
    for i in range(25):
        await reg.register(MCPServerConfig(name=f"c{i}", url="https://x.example"), tenant_ctx=_CTX)
    redis.gets = 0

    records = await reg.list_server_records(tenant_ctx=_CTX)

    assert len(records) == 25
    assert redis.mgets == 1
    assert redis.gets == 0
    assert [sid for sid, _ in records] == sorted(sid for sid, _ in records)
