"""KB-51: the knowledge generation is a column, never part of tenant_id.

The generation scope ``'<tenant>#gN'`` used to be passed to the L2 backend as the
tenant id, so it was written into ``semantic_cache_entries.tenant_id`` (VARCHAR(32))
and used as the RLS GUC — every durable write failed (silently) once a tenant had
changed its knowledge, and real 36-char tenant ids never fit at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.rag.semantic_cache import SemanticCache
from app.rag.vector_cache_backend import InMemoryCacheBackend
from app.tenancy.context import PlanTier, TenantContext

_TID = "0b6f7d1e-3a52-4c1b-9f0e-8d2a6b4c1e77"  # a real (36-char) tenant id
_EMB = [0.1, 0.2, 0.3, 0.4]


class _RecordingBackend(InMemoryCacheBackend):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str, int]] = []

    async def get_similar(  # type: ignore[override]
        self,
        embedding: list[float],
        tenant_id: str,
        threshold: float = 0.92,
        *,
        generation: int = 0,
    ) -> dict[str, Any] | None:
        self.calls.append(("get", tenant_id, generation))
        return await super().get_similar(embedding, tenant_id, threshold, generation=generation)

    async def store(  # type: ignore[override]
        self,
        query: str,
        embedding: list[float],
        response: str,
        tenant_id: str,
        *,
        generation: int = 0,
    ) -> None:
        self.calls.append(("store", tenant_id, generation))
        await super().store(query, embedding, response, tenant_id, generation=generation)


async def test_backend_receives_the_real_tenant_id_and_the_generation() -> None:
    backend = _RecordingBackend()
    cache = SemanticCache(backend=backend)
    await cache.invalidate_tenant(_TID)  # generation 1
    await cache.store_async(_EMB, "q", "answer", _TID)
    other = SemanticCache(backend=backend)
    other._generation[_TID] = 1  # same shared generation as the writer
    hit = await other.get_similar(_EMB, _TID)
    assert hit is not None and hit.response == "answer"
    assert all(tid == _TID for _, tid, _ in backend.calls), backend.calls
    assert ("store", _TID, 1) in backend.calls and ("get", _TID, 1) in backend.calls


async def test_bumped_generation_misses_the_old_entry() -> None:
    backend = InMemoryCacheBackend()
    cache = SemanticCache(backend=backend)
    await cache.store_async(_EMB, "q", "old", _TID)
    await cache.invalidate_tenant(_TID)
    fresh = SemanticCache(backend=backend)
    fresh._generation[_TID] = 1
    assert await fresh.get_similar(_EMB, _TID) is None


async def test_backend_store_failure_is_counted_not_swallowed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.observability.metrics import KNOWLEDGE_FAILURE_TOTAL

    class _Broken(InMemoryCacheBackend):
        async def store(self, *a: Any, **k: Any) -> None:  # type: ignore[override]
            raise RuntimeError("value too long for type character varying(32)")

    before = KNOWLEDGE_FAILURE_TOTAL.labels("semantic_cache", "backend_store")._value.get()
    await SemanticCache(backend=_Broken()).store_async(_EMB, "q", "answer", _TID)
    after = KNOWLEDGE_FAILURE_TOTAL.labels("semantic_cache", "backend_store")._value.get()
    assert after == before + 1


async def test_clear_removes_every_generation() -> None:
    backend = InMemoryCacheBackend()
    cache = SemanticCache(backend=backend)
    await cache.store_async(_EMB, "q", "g0", _TID)
    await cache.invalidate_tenant(_TID)
    await cache.store_async(_EMB, "q", "g1", _TID)
    await cache.clear_async(tenant_ctx=TenantContext(_TID, PlanTier.FREE, "k"))
    for gen in (0, 1):
        assert await backend.get_similar(_EMB, _TID, 0.5, generation=gen) is None
