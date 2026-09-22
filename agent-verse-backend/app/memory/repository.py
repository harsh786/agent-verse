"""Async canonical memory repository protocol and in-memory reference adapter."""

from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol

from app.coordination.store import OptimisticConflictError
from app.memory.contracts import (
    MemoryFeedback,
    MemoryKind,
    MemoryRecallHit,
    MemoryRecallRequest,
    MemoryRecord,
    MemoryWriteRequest,
)
from app.memory.retention import resolve_expires_at

Embedder = Callable[[str], Awaitable[tuple[float, ...]]]


def _has_evidence(evidence_refs: tuple[str, ...]) -> bool:
    """True when at least one evidence ref is a non-blank string.

    A tuple of only empty/whitespace strings is not real evidence — treating it
    as such would let a claim with no actual evidence bypass the quarantine
    gate that an empty ``evidence_refs`` tuple is meant to trigger.
    """
    return any(ref.strip() for ref in evidence_refs)


class MemoryRepository(Protocol):
    async def write(self, request: MemoryWriteRequest) -> MemoryRecord: ...
    async def recall(self, request: MemoryRecallRequest) -> tuple[MemoryRecallHit, ...]: ...
    async def feedback(self, feedback: MemoryFeedback) -> MemoryRecord: ...
    async def update_lifecycle(
        self, tenant_id: str, memory_id: str, *, state: str, expected_version: int
    ) -> MemoryRecord: ...
    async def purge_expired(self, tenant_id: str, *, now: datetime) -> int: ...
    async def list_records(
        self,
        tenant_id: str,
        *,
        memory_kinds: frozenset[MemoryKind] | None = None,
        source_goal_id: str | None = None,
        limit: int = 100,
    ) -> tuple[MemoryRecord, ...]: ...


class InMemoryMemoryRepository:
    def __init__(self, *, embedder: Embedder | None = None, maximum_records: int = 10_000) -> None:
        self._embedder = embedder
        self._maximum = maximum_records
        self._records: dict[tuple[str, str], MemoryRecord] = {}
        self._commands: dict[tuple[str, str], MemoryRecord] = {}
        self._feedback: dict[tuple[str, str, str], MemoryFeedback] = {}
        self._lock = asyncio.Lock()

    async def write(self, request: MemoryWriteRequest) -> MemoryRecord:
        from opentelemetry import trace as _trace

        _tracer = _trace.get_tracer(__name__)
        with _tracer.start_as_current_span("memory.write") as span:
            span.set_attribute("tenant_id", request.tenant_id)
            span.set_attribute("memory_kind", request.memory_kind)
        command = (request.tenant_id, request.idempotency_key)
        async with self._lock:
            prior = self._commands.get(command)
            if prior is not None:
                return prior
            if len(self._records) >= self._maximum:
                raise RuntimeError("memory repository capacity exceeded")
            embedding = await self._embedder(request.content) if self._embedder else None
            if embedding is not None and len(embedding) != 1536:
                raise ValueError("memory embedder returned incompatible dimension")
            now = datetime.now(UTC)
            identifier = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{request.tenant_id}:{request.memory_kind}:{request.idempotency_key}",
            ).hex
            quarantined = any(
                marker in request.content.casefold()
                for marker in ("ignore previous instructions", "reveal secret", "override policy")
            )
            sensitive = request.classification in {"confidential", "restricted"}
            record = MemoryRecord(
                memory_id=identifier,
                tenant_id=request.tenant_id,
                memory_kind=request.memory_kind,
                content_ref=f"memory://encrypted/{identifier}"
                if sensitive
                else f"memory://{identifier}",
                safe_summary=("[REDACTED]" if sensitive else request.content[:4_000]),
                source_goal_id=request.source_goal_id,
                source_execution_id=request.source_execution_id,
                evidence_refs=request.evidence_refs,
                classification=request.classification,
                agent_id=request.agent_id,
                collection_id=request.collection_id,
                source=request.source,
                confidence=request.confidence,
                lifecycle_state="quarantined"
                if quarantined or not _has_evidence(request.evidence_refs)
                else "active",
                version=1,
                embedding_model="memory-embedding-v1",
                embedding_dimension=1536,
                embedding=embedding,
                created_at=now,
                updated_at=now,
                expires_at=resolve_expires_at(request.retention_policy_id, now),
                retention_policy_id=request.retention_policy_id,
                idempotency_key=request.idempotency_key,
            )
            self._records[(record.tenant_id, record.memory_id)] = record
            self._commands[command] = record
            return record

    async def recall(self, request: MemoryRecallRequest) -> tuple[MemoryRecallHit, ...]:
        from opentelemetry import trace as _trace

        _tracer = _trace.get_tracer(__name__)
        with _tracer.start_as_current_span("memory.recall") as span:
            span.set_attribute("tenant_id", request.tenant_id)
            span.set_attribute("query_len", len(request.query))
        query_embedding = await self._embedder(request.query) if self._embedder else None
        ranked: list[MemoryRecallHit] = []
        for record in self._records.values():
            if (
                record.tenant_id != request.tenant_id
                or record.memory_kind not in request.memory_kinds
            ):
                continue
            if record.classification not in request.allowed_data_classes:
                continue
            if not _matches_scope(record, request):
                continue
            allowed_states = {"active"}
            if request.include_disputed:
                allowed_states.add("disputed")
            if (
                record.lifecycle_state not in allowed_states
                or record.confidence < request.min_confidence
            ):
                continue
            if record.expires_at is not None and record.expires_at <= request.as_of:
                continue
            semantic = _similarity(
                query_embedding, record.embedding, request.query, record.safe_summary
            )
            age_days = max(0, (request.as_of - record.updated_at).days)
            recency = max(0, 10_000 - age_days * 100)
            final = (
                semantic * 5
                + recency * 2
                + record.confidence * 2
                + record.outcome_score
                + record.effectiveness_score
            ) // 9
            ranked.append(
                MemoryRecallHit(
                    record=record,
                    semantic_score=semantic,
                    recency_score=recency,
                    outcome_score=record.outcome_score,
                    effectiveness_score=record.effectiveness_score,
                    final_score=final,
                    applicability_reason="semantic and lifecycle eligible",
                    provenance_status="verified" if record.evidence_refs else "missing",
                )
            )
        ordered = sorted(ranked, key=lambda item: (-item.final_score, item.record.memory_id))
        selected: list[MemoryRecallHit] = []
        tokens = 0
        for hit in ordered:
            size = max(1, len(hit.record.safe_summary.split()))
            if tokens + size > request.token_budget:
                continue
            selected.append(hit)
            tokens += size
            if len(selected) >= request.top_k:
                break
        return tuple(selected)

    async def feedback(self, feedback: MemoryFeedback) -> MemoryRecord:
        key = (feedback.tenant_id, feedback.memory_id, feedback.execution_id)
        async with self._lock:
            record_key = (feedback.tenant_id, feedback.memory_id)
            record = self._records.get(record_key)
            if record is None:
                raise KeyError("memory not found")
            if key in self._feedback:
                return record
            helpful = record.helpful_count + int(feedback.was_helpful)
            harmful = record.harmful_count + int(feedback.was_harmful)
            updated = record.model_copy(
                update={
                    "recall_count": record.recall_count + int(feedback.was_used),
                    "helpful_count": helpful,
                    "harmful_count": harmful,
                    "effectiveness_score": max(-10_000, min(10_000, (helpful - harmful) * 1000)),
                    "outcome_score": feedback.outcome_score,
                    "version": record.version + 1,
                    "updated_at": feedback.recorded_at,
                    "lifecycle_state": "quarantined"
                    if feedback.was_harmful
                    else record.lifecycle_state,
                }
            )
            self._feedback[key] = feedback
            self._records[record_key] = updated
            return updated

    async def update_lifecycle(
        self, tenant_id: str, memory_id: str, *, state: str, expected_version: int
    ) -> MemoryRecord:
        async with self._lock:
            key = (tenant_id, memory_id)
            record = self._records.get(key)
            if record is None:
                raise KeyError("memory not found")
            if record.version != expected_version:
                raise OptimisticConflictError("stale memory version")
            updated = record.model_copy(
                update={
                    "lifecycle_state": state,
                    "version": record.version + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
            validated = MemoryRecord.model_validate(updated.model_dump())
            self._records[key] = validated
            return validated

    async def purge_expired(self, tenant_id: str, *, now: datetime) -> int:
        """Hard-delete this tenant's records whose retention window has elapsed."""
        async with self._lock:
            expired = [
                key
                for key, record in self._records.items()
                if record.tenant_id == tenant_id
                and record.expires_at is not None
                and record.expires_at <= now
            ]
            for key in expired:
                del self._records[key]
            return len(expired)

    async def list_records(
        self,
        tenant_id: str,
        *,
        memory_kinds: frozenset[MemoryKind] | None = None,
        source_goal_id: str | None = None,
        limit: int = 100,
    ) -> tuple[MemoryRecord, ...]:
        """List a tenant's canonical records, newest first, with optional filters.

        Read model for the memory inspector: exposes ``memory_kind`` and
        ``source_goal_id`` goal-linkage. Tenant-scoped; honest empty when none.
        """
        async with self._lock:
            records = [
                record for (tid, _mid), record in self._records.items() if tid == tenant_id
            ]
        if memory_kinds:
            records = [r for r in records if r.memory_kind in memory_kinds]
        if source_goal_id is not None:
            records = [r for r in records if r.source_goal_id == source_goal_id]
        records.sort(key=lambda r: (r.created_at, r.memory_id), reverse=True)
        return tuple(records[:limit])


def _matches_scope(record: MemoryRecord, request: MemoryRecallRequest) -> bool:
    """Apply optional agent/collection/source scoping filters (None = no filter)."""
    if request.agent_id is not None and record.agent_id != request.agent_id:
        return False
    if request.collection_id is not None and record.collection_id != request.collection_id:
        return False
    return not (request.source is not None and record.source != request.source)


def _similarity(
    query: tuple[float, ...] | None,
    candidate: tuple[float, ...] | None,
    query_text: str,
    candidate_text: str,
) -> int:
    if query is not None and candidate is not None:
        numerator = sum(left * right for left, right in zip(query, candidate, strict=True))
        denominator = math.sqrt(sum(value * value for value in query)) * math.sqrt(
            sum(value * value for value in candidate)
        )
        return max(0, min(10_000, int((numerator / denominator if denominator else 0) * 10_000)))
    left = set(query_text.casefold().split())
    right = set(candidate_text.casefold().split())
    return len(left & right) * 10_000 // max(1, len(left | right))


__all__ = ["InMemoryMemoryRepository", "MemoryRepository"]
