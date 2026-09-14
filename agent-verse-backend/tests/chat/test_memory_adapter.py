"""Phase 1 — LongTermMemoryStore -> ChatService memory-recall hook adapter."""

from __future__ import annotations

from typing import Any

from app.chat.memory_adapter import build_memory_recall


class _FakeLTM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def recall_async(self, query: str, tenant_ctx: Any, top_k: int = 5) -> list[Any]:
        self.calls.append((query, tenant_ctx.tenant_id))

        class _M:
            def __init__(self, c: str) -> None:
                self.content = c

        return [_M("prefers oat milk"), _M("")]  # empty content is skipped


async def test_adapter_maps_store_to_string_list_with_tenant_ctx() -> None:
    store = _FakeLTM()
    recall = build_memory_recall(store, top_k=3)
    out = await recall("coffee?", "tenant-42")
    assert out == ["prefers oat milk"]  # empties filtered
    assert store.calls == [("coffee?", "tenant-42")]


async def test_adapter_degrades_on_store_error() -> None:
    class _Boom:
        async def recall_async(self, *a: Any, **k: Any) -> list[Any]:
            raise RuntimeError("db down")

    recall = build_memory_recall(_Boom())
    assert await recall("q", "t") == []
