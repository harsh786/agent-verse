"""Memory consolidation - dedup, merge, and lifecycle management."""
from __future__ import annotations
import logging
import datetime
from typing import Any

_log = logging.getLogger(__name__)


class MemoryConsolidator:
    """Consolidates memory: dedup, merge similar entries, manage lifecycle."""

    async def consolidate(self, tenant_id: str, memory_store: dict) -> dict[str, Any]:
        """Run consolidation for a tenant's memories.

        1. Find duplicate/similar entries and merge them
        2. Mark entries older than threshold as stale
        3. Archive very old stale entries
        4. Return stats
        """
        stats = {
            "merged": 0,
            "marked_stale": 0,
            "archived": 0,
            "total_before": 0,
            "total_after": 0,
        }

        memories = [
            v for k, v in memory_store.items()
            if k.startswith(f"{tenant_id}:")
            and v.get("lifecycle_state") not in ("deleted", "archived")
        ]
        stats["total_before"] = len(memories)

        # Mark old memories as stale (older than 30 days without update)
        cutoff_stale = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
        cutoff_archive = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=90)

        for memory in memories:
            updated_str = memory.get("updated_at", "")
            if not updated_str:
                continue
            try:
                updated_dt = datetime.datetime.fromisoformat(
                    updated_str.rstrip("Z")
                ).replace(tzinfo=datetime.timezone.utc)
            except Exception:
                continue

            if memory.get("lifecycle_state") == "active" and updated_dt < cutoff_stale:
                memory["lifecycle_state"] = "stale"
                stats["marked_stale"] += 1
            elif memory.get("lifecycle_state") == "stale" and updated_dt < cutoff_archive:
                memory["lifecycle_state"] = "archived"
                stats["archived"] += 1

        # Find and merge duplicate content
        seen_content: dict[str, str] = {}  # content_hash → memory_id
        for memory in memories:
            if memory.get("lifecycle_state") in ("deleted", "archived"):
                continue
            # Hash the content for dedup
            content = memory.get("content", "").lower().strip()
            content_key = content[:100]  # First 100 chars as key

            if content_key in seen_content:
                # Duplicate found - keep the higher confidence one
                existing_id = seen_content[content_key]
                existing_key = f"{tenant_id}:{existing_id}"
                existing = memory_store.get(existing_key)
                if existing and existing.get("confidence", 0) < memory.get("confidence", 0):
                    # New one is better - archive the old
                    if existing:
                        existing["lifecycle_state"] = "archived"
                    seen_content[content_key] = memory["memory_id"]
                else:
                    # Keep existing, archive new
                    memory["lifecycle_state"] = "archived"
                stats["merged"] += 1
            else:
                seen_content[content_key] = memory["memory_id"]

        remaining = [
            v for k, v in memory_store.items()
            if k.startswith(f"{tenant_id}:")
            and v.get("lifecycle_state") not in ("deleted", "archived")
        ]
        stats["total_after"] = len(remaining)
        return stats


# Singleton
memory_consolidator = MemoryConsolidator()
