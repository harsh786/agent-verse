"""Memory 2.0 consolidation — lifecycle management over a dict-backed store.

Duplicate detection is delegated to the canonical
``app.memory.consolidation.MemoryConsolidator`` (jaccard keyword clustering) so
there is a *single* source of truth for "which memories are duplicates". This
module previously shipped its own first-100-character prefix comparison, which
shadowed the real clusterer and silently missed reworded near-duplicates.

The dict-store lifecycle API (``consolidate(tenant_id, memory_store) -> stats``)
is kept because the memory_v2 REST surface (``app/api/memory_v2.py``) and its
tests depend on it; only the dedup decision now runs through the real
consolidator.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any

from app.memory.consolidation import MemoryConsolidator as _CanonicalConsolidator

_log = logging.getLogger(__name__)

_STALE_AFTER_DAYS = 30
_ARCHIVE_AFTER_DAYS = 90
_INACTIVE_STATES = ("deleted", "archived")


class MemoryConsolidator:
    """Consolidate a tenant's memories: age-based lifecycle + canonical dedup."""

    def __init__(self, *, similarity_cutoff: float = 0.5) -> None:
        # cluster_threshold=2 so that any cluster containing a duplicate is
        # actionable (the canonical consolidator's own merge default is 3).
        self._clusterer = _CanonicalConsolidator(
            cluster_threshold=2, similarity_cutoff=similarity_cutoff
        )

    async def consolidate(self, tenant_id: str, memory_store: dict) -> dict[str, Any]:
        """Run consolidation for a tenant's memories.

        1. Mark active entries older than the stale threshold as ``stale``.
        2. Archive ``stale`` entries older than the archive threshold.
        3. Merge near-duplicates (via canonical jaccard clustering), keeping the
           highest-confidence entry and archiving the rest.
        4. Return stats.
        """
        stats: dict[str, int] = {
            "merged": 0,
            "marked_stale": 0,
            "archived": 0,
            "total_before": 0,
            "total_after": 0,
        }

        prefix = f"{tenant_id}:"
        memories = [
            v
            for k, v in memory_store.items()
            if k.startswith(prefix) and v.get("lifecycle_state") not in _INACTIVE_STATES
        ]
        stats["total_before"] = len(memories)

        now = datetime.datetime.now(datetime.UTC)
        cutoff_stale = now - datetime.timedelta(days=_STALE_AFTER_DAYS)
        cutoff_archive = now - datetime.timedelta(days=_ARCHIVE_AFTER_DAYS)

        for memory in memories:
            updated = self._parse_timestamp(memory.get("updated_at", ""))
            if updated is None:
                continue
            state = memory.get("lifecycle_state")
            if state == "active" and updated < cutoff_stale:
                memory["lifecycle_state"] = "stale"
                stats["marked_stale"] += 1
            elif state == "stale" and updated < cutoff_archive:
                memory["lifecycle_state"] = "archived"
                stats["archived"] += 1

        # Delegate duplicate detection to the canonical clusterer.
        active = [m for m in memories if m.get("lifecycle_state") not in _INACTIVE_STATES]
        for cluster in self._clusterer.cluster_memories(active):
            if len(cluster) < 2:
                continue
            survivor = max(cluster, key=lambda m: m.get("confidence", 0))
            for memory in cluster:
                if memory is survivor or memory.get("lifecycle_state") in _INACTIVE_STATES:
                    continue
                memory["lifecycle_state"] = "archived"
                stats["merged"] += 1

        remaining = [
            v
            for k, v in memory_store.items()
            if k.startswith(prefix) and v.get("lifecycle_state") not in _INACTIVE_STATES
        ]
        stats["total_after"] = len(remaining)
        return stats

    @staticmethod
    def _parse_timestamp(value: str) -> datetime.datetime | None:
        if not value:
            return None
        try:
            return datetime.datetime.fromisoformat(value.rstrip("Z")).replace(
                tzinfo=datetime.UTC
            )
        except ValueError:
            return None


# Singleton
memory_consolidator = MemoryConsolidator()
