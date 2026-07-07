"""ContextBudgetManager — per-step context management: dedup, token cap, relevance re-ranking.

doc-2 §12 explicitly requires this file:
  app/rag/context_manager.py  ← ContextBudgetManager, dedup, token cap
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_CHARS_PER_TOKEN = 4


@dataclass
class ManagedContext:
    chunks: list[dict[str, Any]]
    total_tokens: int
    dedup_removed: int
    budget_remaining: int
    sources_used: list[str] = field(default_factory=list)


class ContextBudgetManager:
    """Per-step context manager: dedup + token cap + relevance re-ranking."""

    def __init__(self, max_tokens: int = 2000) -> None:
        self.max_tokens = max_tokens
        self._seen_chunk_ids: set[str] = set()

    def mark_seen(self, chunk_id: str) -> None:
        self._seen_chunk_ids.add(chunk_id)

    def reset(self) -> None:
        self._seen_chunk_ids.clear()

    def prepare(
        self,
        chunks: list[dict[str, Any]],
        step_query: str = "",
    ) -> ManagedContext:
        if not chunks:
            return ManagedContext(chunks=[], total_tokens=0, dedup_removed=0,
                                  budget_remaining=self.max_tokens)

        # 1. Remove already-seen chunks (cross-iteration dedup)
        seen_filtered = [c for c in chunks if c.get("chunk_id") not in self._seen_chunk_ids]

        # 2. Deduplicate by content
        seen_content: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for c in seen_filtered:
            content = c.get("content", "")
            if content not in seen_content:
                seen_content.add(content)
                deduped.append(c)
        dedup_removed = len(chunks) - len(deduped)

        # 3. Re-rank by step query relevance if provided
        if step_query:
            query_lower = step_query.lower()
            query_words = set(query_lower.split())

            def relevance(c: dict[str, Any]) -> float:
                content_lower = c.get("content", "").lower()
                word_overlap = sum(1 for w in query_words if w in content_lower)
                return c.get("score", 0.5) + 0.1 * word_overlap

            deduped = sorted(deduped, key=relevance, reverse=True)

        # 4. Apply token budget
        selected: list[dict[str, Any]] = []
        token_count = 0
        for c in deduped:
            tokens = max(1, len(c.get("content", "")) // _CHARS_PER_TOKEN)
            if token_count + tokens > self.max_tokens and selected:
                break
            selected.append(c)
            token_count += tokens
            if c.get("chunk_id"):
                self._seen_chunk_ids.add(c["chunk_id"])

        sources = list({c.get("source_url", "") for c in selected if c.get("source_url")})
        return ManagedContext(
            chunks=selected, total_tokens=token_count, dedup_removed=dedup_removed,
            budget_remaining=max(0, self.max_tokens - token_count), sources_used=sources,
        )
