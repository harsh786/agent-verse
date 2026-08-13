"""Tenant-owned evidence-linked knowledge graph memory lifecycle."""

from __future__ import annotations

import asyncio
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class KnowledgeFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_id: str
    tenant_id: str
    subject: str
    predicate: str
    object: str
    evidence_refs: tuple[str, ...]
    classification: str
    confidence: int
    lifecycle_state: str = "active"
    expires_at: datetime | None = None
    version: int = 1


class KnowledgeGraphMemory:
    def __init__(self) -> None:
        self._facts: dict[tuple[str, str, str, str], KnowledgeFact] = {}
        self._lock = asyncio.Lock()

    async def merge(self, fact: KnowledgeFact) -> KnowledgeFact:
        if not fact.evidence_refs:
            raise ValueError("knowledge fact requires provenance")
        key = (
            fact.tenant_id,
            fact.subject.casefold(),
            fact.predicate.casefold(),
            fact.object.casefold(),
        )
        async with self._lock:
            prior = self._facts.get(key)
            if prior is not None:
                merged = prior.model_copy(
                    update={
                        "evidence_refs": tuple(
                            sorted(set(prior.evidence_refs) | set(fact.evidence_refs))
                        ),
                        "confidence": max(prior.confidence, fact.confidence),
                        "version": prior.version + 1,
                    }
                )
                self._facts[key] = merged
                return merged
            self._facts[key] = fact
            return fact

    async def query(self, tenant_id: str, *, subject: str) -> tuple[KnowledgeFact, ...]:
        return tuple(
            item
            for item in self._facts.values()
            if item.tenant_id == tenant_id
            and item.subject.casefold() == subject.casefold()
            and item.lifecycle_state == "active"
        )


__all__ = ["KnowledgeFact", "KnowledgeGraphMemory"]
