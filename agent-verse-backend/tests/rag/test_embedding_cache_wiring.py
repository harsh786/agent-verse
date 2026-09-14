"""Embedding cache wiring in the gateway embed path (opt-in)."""
from __future__ import annotations

from types import SimpleNamespace

import app.rag.gateway as gw


class _Embedder:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, request):
        self.calls += 1
        return SimpleNamespace(
            embeddings=[[0.1, 0.2, 0.3] for _ in request.texts],
            model="m",
            total_tokens=5,
        )


class _Guard:
    async def reserve(self, _op: str) -> int:
        return 0

    def record_tokens(self, _i: int, _t: int) -> None:
        pass


def _req(texts):
    return SimpleNamespace(texts=texts, model="m")


async def test_cache_disabled_by_default_always_calls_embedder(monkeypatch) -> None:
    monkeypatch.setattr(gw._EMBED_CACHE, "_l1", type(gw._EMBED_CACHE._l1)())  # fresh L1
    emb = _Embedder()
    be = gw._BudgetedEmbedder(emb, _Guard())
    monkeypatch.setattr(gw._BudgetedEmbedder, "_cache_enabled", staticmethod(lambda: False))
    await be.embed(_req(["hello world"]))
    await be.embed(_req(["hello world"]))
    assert emb.calls == 2  # no caching → both hit the embedder


async def test_cache_enabled_short_circuits_repeat(monkeypatch) -> None:
    monkeypatch.setattr(gw._EMBED_CACHE, "_l1", type(gw._EMBED_CACHE._l1)())  # fresh L1
    emb = _Embedder()
    be = gw._BudgetedEmbedder(emb, _Guard())
    monkeypatch.setattr(gw._BudgetedEmbedder, "_cache_enabled", staticmethod(lambda: True))
    r1 = await be.embed(_req(["hello world"]))
    r2 = await be.embed(_req(["hello world"]))  # identical → served from cache
    assert emb.calls == 1
    assert r1.embeddings == r2.embeddings
