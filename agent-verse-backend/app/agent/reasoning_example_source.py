"""Tenant-scoped example retrieval for Few-Shot CoT."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from app.agent.patterns.reasoning_contracts import ReasoningExample


class ReasoningExampleSource(Protocol):
    async def retrieve(
        self,
        tenant_id: str,
        query: str,
        limit: int,
        minimum_score: float,
    ) -> tuple[ReasoningExample, ...]: ...


class InMemoryReasoningExampleSource:
    def __init__(self, examples: Mapping[str, tuple[ReasoningExample, ...]]) -> None:
        self._examples = dict(examples)

    async def retrieve(
        self,
        tenant_id: str,
        query: str,
        limit: int,
        minimum_score: float,
    ) -> tuple[ReasoningExample, ...]:
        del query
        ranked = sorted(
            (
                item
                for item in self._examples.get(tenant_id, ())
                if item.relevance_score >= minimum_score
            ),
            key=lambda item: (-item.relevance_score, item.example_id),
        )
        selected: list[ReasoningExample] = []
        seen_hashes: set[str] = set()
        for item in ranked:
            if item.content_sha256 in seen_hashes:
                continue
            seen_hashes.add(item.content_sha256)
            selected.append(item)
            if len(selected) >= max(0, min(limit, 4)):
                break
        return tuple(selected)


__all__ = ["InMemoryReasoningExampleSource", "ReasoningExampleSource"]
