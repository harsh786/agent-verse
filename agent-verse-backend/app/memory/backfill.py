"""Idempotent compatibility-memory backfill through the canonical repository."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Protocol, cast

from app.memory.contracts import Classification, MemoryKind, MemoryWriteRequest
from app.memory.repository import MemoryRepository


@dataclass(frozen=True, slots=True)
class LegacyMemoryRow:
    tenant_id: str
    source_table: str
    source_id: str
    memory_kind: str
    content: str
    source_goal_id: str
    source_execution_id: str
    evidence_refs: tuple[str, ...]
    classification: str = "internal"
    confidence: int = 5000


@dataclass(frozen=True, slots=True)
class BackfillCheckpoint:
    tenant_id: str
    source_table: str
    source_id: str


class CheckpointWriter(Protocol):
    async def __call__(self, checkpoint: BackfillCheckpoint) -> None: ...


async def backfill_memory_rows(
    repository: MemoryRepository,
    rows: Iterable[LegacyMemoryRow],
    *,
    checkpoint: CheckpointWriter,
    batch_size: int = 100,
) -> AsyncIterator[BackfillCheckpoint]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    marker: BackfillCheckpoint | None = None
    count = 0
    for index, row in enumerate(rows, start=1):
        count = index
        await repository.write(
            MemoryWriteRequest(
                tenant_id=row.tenant_id,
                memory_kind=cast(MemoryKind, row.memory_kind),
                content=row.content,
                source_goal_id=row.source_goal_id,
                source_execution_id=row.source_execution_id,
                evidence_refs=row.evidence_refs,
                classification=cast(Classification, row.classification),
                confidence=row.confidence,
                idempotency_key=f"backfill:{row.source_table}:{row.source_id}",
                retention_policy_id="compatibility-backfill-v1",
            )
        )
        marker = BackfillCheckpoint(row.tenant_id, row.source_table, row.source_id)
        if index % batch_size == 0:
            await checkpoint(marker)
            yield marker
    if marker is not None and count % batch_size:
        await checkpoint(marker)
        yield marker


__all__ = [
    "BackfillCheckpoint",
    "CheckpointWriter",
    "LegacyMemoryRow",
    "backfill_memory_rows",
]
