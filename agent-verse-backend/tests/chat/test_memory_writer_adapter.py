"""Phase 1 — memory-writer adapter over LongTermMemoryStore.store_async."""

from __future__ import annotations

from typing import Any

from app.chat.memory_adapter import build_memory_writer


class _FakeStore:
    def __init__(self) -> None:
        self.stored: list[tuple[str, str, str]] = []

    async def store_async(self, *, memory: Any, tenant_ctx: Any) -> str:
        self.stored.append((memory.content, memory.memory_type, tenant_ctx.tenant_id))
        return "mem-1"


async def test_writer_persists_fact_as_long_term_memory() -> None:
    store = _FakeStore()
    write = build_memory_writer(store)
    await write("I prefer window seats", "tenant-7")
    assert store.stored == [("I prefer window seats", "domain_fact", "tenant-7")]


async def test_writer_degrades_on_store_error() -> None:
    class _Boom:
        async def store_async(self, **k: Any) -> str:
            raise RuntimeError("db down")

    write = build_memory_writer(_Boom())
    await write("x", "t")  # must not raise
