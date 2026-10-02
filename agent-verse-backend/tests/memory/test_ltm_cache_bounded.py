"""MEM-34: the API-singleton LongTermMemoryStore cache is bounded (per tenant
and in tenants); recall hits cannot grow it past the cap."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from app.memory import long_term
from app.memory.long_term import LongTermMemory, LongTermMemoryStore
from app.tenancy.context import PlanTier, TenantContext
from tests._rls_recorder import RlsRecordingDb


def _ctx(t: str) -> TenantContext:
    return TenantContext(tenant_id=t, plan=PlanTier.FREE, api_key_id="k")


def _mem(i: int) -> LongTermMemory:
    return LongTermMemory(content=f"fact {i}", source_goal_id="g", memory_type="domain_fact")


def _cached(store: LongTermMemoryStore) -> int:
    return sum(len(v) for v in store._memories.values())


def test_store_keeps_the_cache_bounded_per_tenant_and_in_tenants() -> None:
    store = LongTermMemoryStore()
    for i in range(10_000):
        store.store(memory=_mem(i), tenant_ctx=_ctx("t-hot"))
    assert len(store._memories["t-hot"]) == long_term._CACHE_PER_TENANT
    # newest kept
    assert store._memories["t-hot"][-1].content == "fact 9999"
    for i in range(long_term._CACHE_TENANTS + 50):
        store.store(memory=_mem(i), tenant_ctx=_ctx(f"t-{i}"))
    assert len(store._memories) == long_term._CACHE_TENANTS
    assert _cached(store) <= long_term._CACHE_TENANTS * long_term._CACHE_PER_TENANT


class _Embedder:
    async def embed(self, _req: Any) -> Any:
        return SimpleNamespace(embeddings=[[0.1] * 8], model="m")


async def test_recall_hits_do_not_grow_the_cache_past_the_cap() -> None:
    now = datetime.now(UTC)
    calls = {"n": 0}

    def rows(sql: str, _p: dict[str, Any]) -> list[Any]:
        if "SELECT id, content" not in sql:
            return []
        calls["n"] += 1
        base = calls["n"] * 1000
        return [
            (f"m{base + i}", f"fact {base + i}", "domain_fact", 0.9, "g", [], now, 0.9)
            for i in range(150)
        ]

    db = RlsRecordingDb(rows_for=rows)
    store = LongTermMemoryStore()
    for _ in range(10):
        await store.recall_async("fact", _ctx("t-r"), top_k=150, db=db, embedder=_Embedder())
    assert calls["n"] == 10
    assert len(store._memories["t-r"]) <= long_term._CACHE_PER_TENANT
