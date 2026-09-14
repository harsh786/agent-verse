"""Embedding cache — avoid re-embedding identical text (Phase 5, T5.1).

Embeddings are deterministic for a (model, text) pair, so caching them removes
repeated provider calls for recurring queries/chunks. Two tiers:

* L1: in-process LRU (fast, per-replica).
* L2: optional Redis (cross-replica), values ``struct``-packed float32.

Keying normalizes only whitespace (collapse runs + strip) — never case — because
embeddings ARE case-sensitive; folding case would serve a subtly wrong vector.
Cache is best-effort: any backend error degrades to a miss, never raises.
"""

from __future__ import annotations

import contextlib
import hashlib
import struct
from collections import OrderedDict
from typing import Any

_NS = "emb"


def _normalize(text: str) -> str:
    # Collapse internal whitespace runs + strip; preserve case (embeddings are
    # case-sensitive) so we never return a vector for a different string.
    return " ".join(text.split())


def _key(model: str, text: str) -> str:
    digest = hashlib.sha256(f"{model}\x00{_normalize(text)}".encode()).hexdigest()
    return f"{_NS}:{model}:{digest}"


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


class EmbeddingCache:
    """L1 LRU + optional async Redis cache for text embeddings."""

    def __init__(self, redis: Any | None = None, *, max_size: int = 4096) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._redis = redis
        self._max_size = max_size
        self._l1: OrderedDict[str, list[float]] = OrderedDict()

    def _l1_get(self, key: str) -> list[float] | None:
        vec = self._l1.get(key)
        if vec is not None:
            self._l1.move_to_end(key)
        return vec

    def _l1_put(self, key: str, vec: list[float]) -> None:
        self._l1[key] = vec
        self._l1.move_to_end(key)
        while len(self._l1) > self._max_size:
            self._l1.popitem(last=False)

    async def get(self, model: str, text: str) -> list[float] | None:
        key = _key(model, text)
        hit = self._l1_get(key)
        if hit is not None:
            return hit
        if self._redis is not None:
            try:
                blob = await self._redis.get(key)
            except Exception:
                return None
            if blob:
                vec = _unpack(blob if isinstance(blob, bytes) else bytes(blob))
                self._l1_put(key, vec)
                return vec
        return None

    async def set(self, model: str, text: str, embedding: list[float]) -> None:
        if not embedding:
            return
        key = _key(model, text)
        self._l1_put(key, list(embedding))
        if self._redis is not None:
            with contextlib.suppress(Exception):  # best-effort L2
                await self._redis.set(key, _pack(embedding))

    async def get_batch(
        self, model: str, texts: list[str]
    ) -> tuple[dict[int, list[float]], list[int]]:
        """Return ``(hits_by_index, miss_indices)`` for a batch of texts.

        Lets callers embed only the misses, then ``set`` them — the batch-embed
        latency win the RAG pipeline needs.
        """
        hits: dict[int, list[float]] = {}
        misses: list[int] = []
        for i, text in enumerate(texts):
            vec = await self.get(model, text)
            if vec is None:
                misses.append(i)
            else:
                hits[i] = vec
        return hits, misses

    def __len__(self) -> int:
        return len(self._l1)
