"""Tests proving memory_v2 consolidation delegates to the *canonical* consolidator.

The memory_v2 module used to ship a private duplicate detector that keyed on the
first 100 characters of each entry's content. That stub is now replaced by
delegation to ``app.memory.consolidation.MemoryConsolidator`` (jaccard keyword
clustering). These tests lock in the real behavior:

* Two entries that share most keywords but differ in their first 100 characters
  MUST be treated as duplicates (the old prefix stub would have missed them).
* The lifecycle stats contract (stale/archive by age, dedup counts) that the
  memory_v2 REST surface depends on is preserved.
"""

from __future__ import annotations

import datetime

import pytest

from app.memory.consolidation import MemoryConsolidator as CanonicalConsolidator
from app.memory_v2.consolidation import MemoryConsolidator, memory_consolidator


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


@pytest.mark.asyncio
async def test_delegates_to_canonical_jaccard_clustering_not_prefix_stub() -> None:
    """Similar content with *different* first-100-chars must still merge.

    The old first-100-char stub keyed dedup on ``content[:100]`` so these two
    entries (same facts, reordered wording) would NOT have merged. The canonical
    jaccard clusterer recognises them as near-duplicates.
    """
    consolidator = MemoryConsolidator()
    a = (
        "The Eiffel Tower is a wrought iron lattice tower located on the Champ "
        "de Mars in Paris France built 1889"
    )
    b = (
        "Paris France is home to the Eiffel Tower a wrought iron lattice tower "
        "on the Champ de Mars built 1889 landmark"
    )
    # Sanity: the two contents differ within the first 100 chars, so the old
    # prefix-key stub could not have detected them as duplicates.
    assert a[:100] != b[:100]

    store = {
        "tenant-x:mem_a": {
            "memory_id": "mem_a",
            "content": a,
            "lifecycle_state": "active",
            "updated_at": _now(),
            "confidence": 0.6,
        },
        "tenant-x:mem_b": {
            "memory_id": "mem_b",
            "content": b,
            "lifecycle_state": "active",
            "updated_at": _now(),
            "confidence": 0.9,
        },
    }

    stats = await consolidator.consolidate("tenant-x", store)

    assert stats["merged"] == 1
    assert stats["total_after"] == 1
    # Higher-confidence entry survives; the other is archived out.
    assert store["tenant-x:mem_b"]["lifecycle_state"] == "active"
    assert store["tenant-x:mem_a"]["lifecycle_state"] == "archived"


@pytest.mark.asyncio
async def test_uses_the_real_consolidator_class() -> None:
    """The delegating consolidator is backed by the canonical implementation."""
    consolidator = MemoryConsolidator()
    assert isinstance(consolidator._clusterer, CanonicalConsolidator)


@pytest.mark.asyncio
async def test_unrelated_entries_are_not_merged() -> None:
    """Entries with no keyword overlap must be left untouched."""
    consolidator = MemoryConsolidator()
    store = {
        "tenant-x:m1": {
            "memory_id": "m1",
            "content": "Quarterly revenue projections spreadsheet finance planning",
            "lifecycle_state": "active",
            "updated_at": _now(),
            "confidence": 0.7,
        },
        "tenant-x:m2": {
            "memory_id": "m2",
            "content": "Kubernetes deployment rollout networking ingress controller",
            "lifecycle_state": "active",
            "updated_at": _now(),
            "confidence": 0.7,
        },
    }
    stats = await consolidator.consolidate("tenant-x", store)
    assert stats["merged"] == 0
    assert stats["total_after"] == 2


@pytest.mark.asyncio
async def test_lifecycle_stale_and_archive_contract_preserved() -> None:
    """Age-based staling/archiving stats stay intact for the REST surface."""
    old = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=35)).isoformat()
    very_old = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=95)).isoformat()
    store = {
        "t:stale": {
            "memory_id": "stale",
            "content": "some active but old fact about widgets",
            "lifecycle_state": "active",
            "updated_at": old,
            "confidence": 0.5,
        },
        "t:archive": {
            "memory_id": "archive",
            "content": "a totally separate stale note about gadgets",
            "lifecycle_state": "stale",
            "updated_at": very_old,
            "confidence": 0.5,
        },
    }
    stats = await consolidator_run(store)
    assert stats["marked_stale"] == 1
    assert stats["archived"] == 1
    assert store["t:stale"]["lifecycle_state"] == "stale"
    assert store["t:archive"]["lifecycle_state"] == "archived"


async def consolidator_run(store: dict) -> dict:
    return await MemoryConsolidator().consolidate("t", store)


@pytest.mark.asyncio
async def test_singleton_exposed() -> None:
    stats = await memory_consolidator.consolidate("empty-tenant", {})
    assert stats["total_before"] == 0
    assert stats["total_after"] == 0
