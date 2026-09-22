"""PostgreSQL/RLS implementation of the canonical async memory repository."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.coordination.store import OptimisticConflictError
from app.db.models.memory import CanonicalMemoryRecord, MemoryFeedbackRow
from app.db.rls import sqlalchemy_rls_context
from app.memory.contracts import (
    MemoryFeedback,
    MemoryKind,
    MemoryRecallHit,
    MemoryRecallRequest,
    MemoryRecord,
    MemoryWriteRequest,
)
from app.memory.repository import Embedder, _has_evidence, _matches_scope, _similarity
from app.memory.retention import resolve_expires_at


class PostgresMemoryRepository:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, embedder: Embedder | None = None
    ) -> None:
        self._sessions = session_factory
        self._embedder = embedder

    async def write(self, request: MemoryWriteRequest) -> MemoryRecord:
        embedding = await self._embedder(request.content) if self._embedder else None
        if embedding is not None and len(embedding) != 1536:
            raise ValueError("memory embedder returned incompatible dimension")
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, request.tenant_id),
        ):
            prior = (
                await db.execute(
                    select(CanonicalMemoryRecord).where(
                        CanonicalMemoryRecord.tenant_id == request.tenant_id,
                        CanonicalMemoryRecord.idempotency_key == request.idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if prior is not None:
                return _record(prior)
            identifier = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{request.tenant_id}:{request.memory_kind}:{request.idempotency_key}",
            ).hex
            # FOLLOW-UP (out of this change's scope: app/db is not touched here):
            # persist request.agent_id / collection_id / source once a migration
            # adds those columns to memory_records. Until then Postgres-backed
            # scope filtering only excludes rows whose (absent) scope is None; the
            # in-memory repository is the fully-scoped reference implementation.
            sensitive = request.classification in {"confidential", "restricted"}
            poisoned = any(
                marker in request.content.casefold()
                for marker in ("ignore previous instructions", "reveal secret", "override policy")
            )
            now = datetime.now(UTC)
            # Assign the retention deadline uniformly for every memory_kind so the
            # kind-agnostic purge paths can physically reclaim the row once it
            # elapses (previously expires_at was never set → nothing ever expired).
            expires_at = resolve_expires_at(request.retention_policy_id, now)
            values = {
                "id": identifier,
                "tenant_id": request.tenant_id,
                "memory_kind": request.memory_kind,
                "content_ref": f"memory://encrypted/{identifier}"
                if sensitive
                else f"memory://{identifier}",
                "safe_summary": "[REDACTED]" if sensitive else request.content[:4_000],
                "source_goal_id": request.source_goal_id,
                "source_execution_id": request.source_execution_id,
                "evidence_refs": list(request.evidence_refs),
                "classification": request.classification,
                "confidence": request.confidence,
                "lifecycle_state": "quarantined"
                if poisoned or not _has_evidence(request.evidence_refs)
                else "active",
                "version": 1,
                "embedding_model": "memory-embedding-v1",
                "embedding_dimension": 1536,
                "embedding": list(embedding) if embedding is not None else None,
                "outcome_score": 0,
                "effectiveness_score": 0,
                "recall_count": 0,
                "helpful_count": 0,
                "harmful_count": 0,
                "retention_policy_id": request.retention_policy_id,
                "idempotency_key": request.idempotency_key,
                "expires_at": expires_at,
                "created_at": now,
                "updated_at": now,
            }
            await db.execute(insert(CanonicalMemoryRecord).values(**values))
            return MemoryRecord(
                memory_id=identifier,
                embedding=embedding,
                **{key: value for key, value in values.items() if key not in {"id", "embedding"}},
            )

    async def recall(self, request: MemoryRecallRequest) -> tuple[MemoryRecallHit, ...]:
        query_embedding = await self._embedder(request.query) if self._embedder else None
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, request.tenant_id),
        ):
            rows = (
                await db.execute(
                    select(CanonicalMemoryRecord)
                    .where(
                        CanonicalMemoryRecord.tenant_id == request.tenant_id,
                        CanonicalMemoryRecord.memory_kind.in_(request.memory_kinds),
                        CanonicalMemoryRecord.classification.in_(request.allowed_data_classes),
                        CanonicalMemoryRecord.confidence >= request.min_confidence,
                    )
                    .limit(500)
                )
            ).scalars()
            candidates = tuple(_record(row) for row in rows)
        hits: list[MemoryRecallHit] = []
        for record in candidates:
            states = {"active"} | ({"disputed"} if request.include_disputed else set())
            if record.lifecycle_state not in states:
                continue
            if not _matches_scope(record, request):
                continue
            if record.expires_at is not None and record.expires_at <= request.as_of:
                continue
            semantic = _similarity(
                query_embedding, record.embedding, request.query, record.safe_summary
            )
            recency = max(0, 10_000 - max(0, (request.as_of - record.updated_at).days) * 100)
            final = (
                semantic * 5
                + recency * 2
                + record.confidence * 2
                + record.outcome_score
                + record.effectiveness_score
            ) // 9
            hits.append(
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
        ordered = sorted(hits, key=lambda item: (-item.final_score, item.record.memory_id))
        selected: list[MemoryRecallHit] = []
        tokens = 0
        for hit in ordered:
            size = max(1, len(hit.record.safe_summary.split()))
            if tokens + size <= request.token_budget:
                selected.append(hit)
                tokens += size
            if len(selected) >= request.top_k:
                break
        return tuple(selected)

    async def feedback(self, feedback: MemoryFeedback) -> MemoryRecord:
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, feedback.tenant_id),
        ):
            memory = (
                await db.execute(
                    select(CanonicalMemoryRecord)
                    .where(CanonicalMemoryRecord.id == feedback.memory_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if memory is None:
                raise KeyError("memory not found")
            feedback_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{feedback.tenant_id}:{feedback.memory_id}:{feedback.execution_id}",
            ).hex
            prior = await db.get(MemoryFeedbackRow, feedback_id)
            if prior is not None:
                return _record(memory)
            await db.execute(
                insert(MemoryFeedbackRow).values(
                    id=feedback_id,
                    tenant_id=feedback.tenant_id,
                    **feedback.model_dump(exclude={"tenant_id"}),
                )
            )
            helpful = memory.helpful_count + int(feedback.was_helpful)
            harmful = memory.harmful_count + int(feedback.was_harmful)
            await db.execute(
                update(CanonicalMemoryRecord)
                .where(CanonicalMemoryRecord.id == memory.id)
                .values(
                    recall_count=memory.recall_count + int(feedback.was_used),
                    helpful_count=helpful,
                    harmful_count=harmful,
                    effectiveness_score=max(-10_000, min(10_000, (helpful - harmful) * 1000)),
                    outcome_score=feedback.outcome_score,
                    version=memory.version + 1,
                    updated_at=feedback.recorded_at,
                    lifecycle_state="quarantined"
                    if feedback.was_harmful
                    else memory.lifecycle_state,
                )
            )
            memory.helpful_count = helpful
            memory.harmful_count = harmful
            memory.recall_count += int(feedback.was_used)
            memory.effectiveness_score = max(-10_000, min(10_000, (helpful - harmful) * 1000))
            memory.outcome_score = feedback.outcome_score
            memory.version += 1
            memory.updated_at = feedback.recorded_at
            if feedback.was_harmful:
                memory.lifecycle_state = "quarantined"
            return _record(memory)

    async def update_lifecycle(
        self, tenant_id: str, memory_id: str, *, state: str, expected_version: int
    ) -> MemoryRecord:
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            row = (
                await db.execute(
                    select(CanonicalMemoryRecord)
                    .where(CanonicalMemoryRecord.id == memory_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                raise KeyError("memory not found")
            if row.version != expected_version:
                raise OptimisticConflictError("stale memory version")
            row.lifecycle_state = state
            row.version += 1
            row.updated_at = datetime.now(UTC)
            return MemoryRecord.model_validate(_record(row).model_dump())

    async def purge_expired(self, tenant_id: str, *, now: datetime) -> int:
        """Hard-delete this tenant's rows whose retention window has elapsed.

        Runs inside the tenant's RLS scope so the delete cannot cross tenants.
        """
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            result = await db.execute(
                delete(CanonicalMemoryRecord).where(
                    CanonicalMemoryRecord.tenant_id == tenant_id,
                    CanonicalMemoryRecord.expires_at.is_not(None),
                    CanonicalMemoryRecord.expires_at <= now,
                )
            )
            return result.rowcount or 0

    async def list_records(
        self,
        tenant_id: str,
        *,
        memory_kinds: frozenset[MemoryKind] | None = None,
        source_goal_id: str | None = None,
        limit: int = 100,
    ) -> tuple[MemoryRecord, ...]:
        """List a tenant's canonical records, newest first, with optional filters.

        Runs inside the tenant's RLS scope so it can only ever read this tenant's
        rows. Exposes ``memory_kind`` and ``source_goal_id`` goal-linkage for the
        memory inspector; honest empty tuple when nothing matches.
        """
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            stmt = select(CanonicalMemoryRecord).where(
                CanonicalMemoryRecord.tenant_id == tenant_id
            )
            if memory_kinds:
                stmt = stmt.where(CanonicalMemoryRecord.memory_kind.in_(memory_kinds))
            if source_goal_id is not None:
                stmt = stmt.where(CanonicalMemoryRecord.source_goal_id == source_goal_id)
            stmt = stmt.order_by(CanonicalMemoryRecord.created_at.desc()).limit(limit)
            rows = (await db.execute(stmt)).scalars()
            return tuple(_record(row) for row in rows)


def _record(row: CanonicalMemoryRecord) -> MemoryRecord:
    embedding = (
        tuple(float(value) for value in row.embedding) if row.embedding is not None else None
    )
    return MemoryRecord(
        memory_id=row.id,
        tenant_id=row.tenant_id,
        memory_kind=row.memory_kind,
        content_ref=row.content_ref,
        safe_summary=row.safe_summary,
        source_goal_id=row.source_goal_id,
        source_execution_id=row.source_execution_id,
        evidence_refs=tuple(row.evidence_refs),
        classification=row.classification,
        # Scope columns are a follow-up migration (see write()); read defensively
        # so the field is populated automatically once the columns land.
        agent_id=getattr(row, "agent_id", None),
        collection_id=getattr(row, "collection_id", None),
        source=getattr(row, "source", None),
        confidence=row.confidence,
        lifecycle_state=row.lifecycle_state,
        version=row.version,
        embedding_model=row.embedding_model,
        embedding_dimension=row.embedding_dimension,
        embedding=embedding,
        outcome_score=row.outcome_score,
        effectiveness_score=row.effectiveness_score,
        recall_count=row.recall_count,
        helpful_count=row.helpful_count,
        harmful_count=row.harmful_count,
        retention_policy_id=row.retention_policy_id,
        idempotency_key=row.idempotency_key,
        created_at=row.created_at,
        updated_at=row.updated_at,
        expires_at=row.expires_at,
    )


__all__ = ["PostgresMemoryRepository"]
