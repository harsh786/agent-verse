"""PostgreSQL/RLS implementation of the canonical async memory repository."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (
    Select,
    cast,
    delete,
    func,
    insert,
    literal,
    or_,
    select,
    text,
    update,
)
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
from app.memory.embedding import MEMORY_EMBEDDING_DIM, fit_memory_vector
from app.memory.repository import Embedder, _has_evidence, _matches_scope, _similarity
from app.memory.retention import resolve_expires_at
from app.memory.sealing import (
    MemoryPayloadCipher,
    SensitiveMemoryUnavailableError,
    default_cipher,
    open_payload,
    seal_payload,
)

_SENSITIVE = frozenset({"confidential", "restricted"})
# Candidate-set bounds for recall: each SQL candidate query fetches at most this
# many rows (deterministically ordered), however large the tenant's memory is.
_MIN_CANDIDATES = 40
_MAX_CANDIDATES = 200
# MEM-09: a tenant with at most this many embedded records is searched exactly
# (its rows via the tenant index, ordered by true distance) instead of through
# the one global HNSW index, whose post-filter can starve a small tenant.
_EXACT_SEARCH_MAX_ROWS = 5_000
# Larger tenants keep the HNSW index, with pgvector >= 0.8 iterative scans so
# the tenant filter cannot empty the candidate set.
_HNSW_EF_SEARCH = 200


def _candidate_limit(request: MemoryRecallRequest) -> int:
    return max(_MIN_CANDIDATES, min(_MAX_CANDIDATES, request.top_k * 8))


def recall_candidate_queries(
    request: MemoryRecallRequest,
    *,
    query_embedding: tuple[float, ...] | None,
    embedding_model: str | None,
    exact_vector: bool = False,
) -> tuple[Select[tuple[CanonicalMemoryRecord]], ...]:
    """The bounded SQL candidate queries behind :meth:`PostgresMemoryRepository.recall`.

    Every eligibility rule (tenant, kind, classification, confidence, lifecycle,
    expiry, agent/collection/source scope) is a WHERE clause, so ineligible rows
    are never loaded. Two deterministically-ordered, LIMITed queries supply the
    candidates that the blended score then ranks:

    * relevance — cosine distance to the query vector over rows embedded by the
      same model (``ix_memory_records_embedding_halfvec``), or pg_trgm similarity
      of the safe summary when no query vector is available;
    * recency — newest first (``ix_memory_records_recall_scope`` /
      ``idx_memory_tenant_kind_lifecycle_updated``).
    """
    model = CanonicalMemoryRecord
    states = ["active", *(["disputed"] if request.include_disputed else [])]
    filters: list[Any] = [
        model.tenant_id == request.tenant_id,
        model.memory_kind.in_(sorted(request.memory_kinds)),
        model.classification.in_(sorted(request.allowed_data_classes)),
        model.confidence >= request.min_confidence,
        model.lifecycle_state.in_(states),
        or_(model.expires_at.is_(None), model.expires_at > request.as_of),
    ]
    if request.agent_id is not None:
        filters.append(model.agent_id == request.agent_id)
    if request.collection_id is not None:
        filters.append(model.collection_id == request.collection_id)
    if request.source is not None:
        filters.append(model.source == request.source)
    limit = _candidate_limit(request)
    relevance: Select[tuple[CanonicalMemoryRecord]]
    if query_embedding is not None:
        same_model = (
            model.embedding_source_model == embedding_model
            if embedding_model is not None
            else model.embedding_source_model.is_(None)
        )
        # The halfvec expression the HNSW index is built on (MEM-38: plain
        # vector HNSW caps at 2000 dims; the column is 2048).
        distance = cast(model.embedding, HALFVEC(MEMORY_EMBEDDING_DIM)).cosine_distance(
            list(query_embedding)
        )
        if exact_vector:
            # ``+ 0`` keeps the ordering exact but makes it unusable by the HNSW
            # index, so the planner filters by tenant first (MEM-09).
            distance = distance + literal(0.0)
        relevance = (
            select(model)
            .where(*filters, model.embedding.is_not(None), same_model)
            .order_by(distance.asc(), model.id)
            .limit(limit)
        )
    else:
        relevance = (
            select(model)
            .where(*filters)
            .order_by(
                func.similarity(model.safe_summary, request.query).desc(),
                model.updated_at.desc(),
                model.id,
            )
            .limit(limit)
        )
    recency = (
        select(model).where(*filters).order_by(model.updated_at.desc(), model.id).limit(limit)
    )
    return (relevance, recency)


class PostgresMemoryRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        embedder: Embedder | None = None,
        cipher: MemoryPayloadCipher | None = None,
    ) -> None:
        self._sessions = session_factory
        self._embedder = embedder
        self._cipher = cipher

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """The session factory the records are written through."""
        return self._sessions

    @property
    def embedding_model(self) -> str | None:
        """The model id stored with (and matched against) this repository's vectors."""
        if self._embedder is None:
            return None
        model_id = getattr(self._embedder, "model_id", None)
        return str(model_id) if model_id else None

    def _get_cipher(self) -> MemoryPayloadCipher:
        if self._cipher is None:
            self._cipher = default_cipher()
        return self._cipher

    async def _embed(self, text: str) -> tuple[float, ...] | None:
        if self._embedder is None:
            return None
        embedding = await self._embedder(text)
        if embedding is None:
            return None
        fitted = fit_memory_vector(embedding)
        if fitted is None:
            raise ValueError("memory embedder returned incompatible dimension")
        return fitted

    async def write(self, request: MemoryWriteRequest) -> MemoryRecord:
        sensitive = request.classification in _SENSITIVE
        # MEM-68: the shared memory-write gate, before any DB work — no caller
        # can store unvetted text through this repository.
        from app.memory.screening import vet_canonical_write

        request = request.model_copy(
            update={
                "content": await vet_canonical_write(
                    request.content,
                    tenant_id=request.tenant_id,
                    goal_id=request.source_goal_id or None,
                    sensitive=sensitive,
                )
            }
        )
        identifier = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"{request.tenant_id}:{request.memory_kind}:{request.idempotency_key}",
        ).hex
        # Sensitive content is sealed before any DB work (fail closed: no vault →
        # the write is refused; it is never stored in plaintext nor left as a
        # dangling reference). It is not embedded either — a vector of it would
        # leak what the [REDACTED] summary hides.
        sealed = (
            seal_payload(
                self._get_cipher(),
                tenant_id=request.tenant_id,
                memory_id=identifier,
                content=request.content,
            )
            if sensitive
            else None
        )
        embedding = None if sensitive else await self._embed(request.content)
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
                "agent_id": request.agent_id,
                "collection_id": request.collection_id,
                "source": request.source,
                "confidence": request.confidence,
                "lifecycle_state": "quarantined"
                if poisoned or not _has_evidence(request.evidence_refs)
                else "active",
                "version": 1,
                "embedding_model": "memory-embedding-v1",
                "embedding_dimension": MEMORY_EMBEDDING_DIM,
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
            await db.execute(
                insert(CanonicalMemoryRecord).values(
                    **values,
                    sealed_content=sealed,
                    embedding_source_model=self.embedding_model
                    if embedding is not None
                    else None,
                )
            )
            return MemoryRecord(
                memory_id=identifier,
                embedding=embedding,
                **{key: value for key, value in values.items() if key not in {"id", "embedding"}},
            )

    async def reembed_pending(self, tenant_id: str, *, limit: int = 200) -> int:
        """Embed a tenant's records that have no vector or a stale-model one (MEM-10).

        Writes whose embedding returned None stayed vector-less forever, and
        vectors of a previous model are never compared at recall. Sensitive
        (sealed) records are never embedded. Bounded per call; returns how many
        records got a current vector. Raises on a DB error.
        """
        model_id = self.embedding_model
        if self._embedder is None or model_id is None:
            return 0
        rec = CanonicalMemoryRecord
        # MEM-38: a record this model cannot embed (too wide) is marked so the
        # sweep skips it and advances; it used to retry the same rows forever.
        unembeddable = f"unembeddable:{model_id}"[:128]
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            pending = (
                await db.execute(
                    select(rec.id, rec.safe_summary)
                    .where(
                        rec.tenant_id == tenant_id,
                        rec.classification.not_in(sorted(_SENSITIVE)),
                        rec.lifecycle_state.in_(["active", "disputed"]),
                        or_(
                            rec.embedding.is_(None),
                            rec.embedding_source_model.is_(None),
                            rec.embedding_source_model != model_id,
                        ),
                        or_(
                            rec.embedding_source_model.is_(None),
                            rec.embedding_source_model != unembeddable,
                        ),
                    )
                    .order_by(rec.id)
                    .limit(max(1, limit))
                )
            ).all()
        done = 0
        checked = getattr(self._embedder, "embed_checked", None)
        for memory_id, summary in pending:
            if checked is not None:
                raw, reason = await checked(str(summary or ""))
                vector = fit_memory_vector(raw) if raw is not None else None
            else:
                try:
                    vector, reason = await self._embed(str(summary or "")), "ok"
                except ValueError:
                    vector, reason = None, "too_wide"
                if vector is None and reason == "ok":
                    reason = "failed"
            if vector is None and reason != "too_wide":
                # Provider outage: stop this tenant's sweep (the next run
                # retries) instead of hammering a failing provider.
                break
            values: dict[str, Any] = (
                {"embedding": list(vector), "embedding_source_model": model_id}
                if vector is not None
                # A stale vector of an older model is dropped with it: nothing
                # can compare against it once the label no longer names it.
                else {"embedding": None, "embedding_source_model": unembeddable}
            )
            async with (
                self._sessions() as db,
                db.begin(),
                sqlalchemy_rls_context(db, tenant_id),
            ):
                await db.execute(
                    update(rec)
                    .where(rec.tenant_id == tenant_id, rec.id == memory_id)
                    .values(**values)
                )
            if vector is not None:
                done += 1
        return done

    async def read_sensitive_content(self, tenant_id: str, memory_id: str) -> str:
        """Open the sealed payload of a sensitive record (tenant RLS-scoped).

        Raises ``KeyError`` when the record is not visible to the tenant and
        :class:`SensitiveMemoryUnavailableError` when it has no sealed payload
        or the payload cannot be opened.
        """
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, tenant_id),
        ):
            row = (
                await db.execute(
                    select(CanonicalMemoryRecord).where(
                        CanonicalMemoryRecord.tenant_id == tenant_id,
                        CanonicalMemoryRecord.id == memory_id,
                    )
                )
            ).scalar_one_or_none()
        if row is None:
            raise KeyError("memory not found")
        sealed = getattr(row, "sealed_content", None)
        if not sealed:
            raise SensitiveMemoryUnavailableError("memory record has no sealed payload")
        return open_payload(
            self._get_cipher(), tenant_id=tenant_id, memory_id=memory_id, sealed=sealed
        )

    async def _iterative_scan_supported(self, db: AsyncSession) -> bool:
        cached: bool | None = getattr(self, "_iterative_scan", None)
        if cached is not None:
            return cached
        version = (
            await db.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'"))
        ).scalar()
        try:
            parts = tuple(int(p) for p in str(version or "0").split(".")[:2])
        except ValueError:
            parts = (0, 0)
        self._iterative_scan = parts >= (0, 8)
        return self._iterative_scan

    async def _use_exact_vector_search(self, db: AsyncSession, tenant_id: str) -> bool:
        """True for a small tenant (exact search); tunes HNSW for a large one."""
        model = CanonicalMemoryRecord
        bounded = (
            select(model.id)
            .where(model.tenant_id == tenant_id, model.embedding.is_not(None))
            .limit(_EXACT_SEARCH_MAX_ROWS + 1)
            .subquery()
        )
        count = int((await db.execute(select(func.count()).select_from(bounded))).scalar() or 0)
        if count <= _EXACT_SEARCH_MAX_ROWS:
            return True
        if await self._iterative_scan_supported(db):
            await db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
            await db.execute(text(f"SET LOCAL hnsw.ef_search = {_HNSW_EF_SEARCH}"))
        return False

    async def recall(self, request: MemoryRecallRequest) -> tuple[MemoryRecallHit, ...]:
        query_embedding = await self._embed(request.query)
        embedding_model = self.embedding_model
        async with (
            self._sessions() as db,
            db.begin(),
            sqlalchemy_rls_context(db, request.tenant_id),
        ):
            exact = (
                await self._use_exact_vector_search(db, request.tenant_id)
                if query_embedding is not None
                else False
            )
            rows: dict[str, CanonicalMemoryRecord] = {}
            for stmt in recall_candidate_queries(
                request,
                query_embedding=query_embedding,
                embedding_model=embedding_model,
                exact_vector=exact,
            ):
                for row in (await db.execute(stmt)).scalars():
                    rows.setdefault(row.id, row)
            candidates = [
                (_record(row), getattr(row, "embedding_source_model", None))
                for row in rows.values()
            ]
        hits: list[MemoryRecallHit] = []
        states = {"active"} | ({"disputed"} if request.include_disputed else set())
        for record, source_model in candidates:
            # Defence in depth: the SQL already filtered on each of these.
            if record.lifecycle_state not in states:
                continue
            if not _matches_scope(record, request):
                continue
            if record.expires_at is not None and record.expires_at <= request.as_of:
                continue
            # Vectors are compared only when both came from the same model.
            comparable = (
                record.embedding
                if query_embedding is not None and source_model == embedding_model
                else None
            )
            semantic = _similarity(
                query_embedding if comparable is not None else None,
                comparable,
                request.query,
                record.safe_summary,
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
                    .where(
                        CanonicalMemoryRecord.id == feedback.memory_id,
                        # Explicit tenant predicate as well as RLS (BYPASSRLS roles).
                        CanonicalMemoryRecord.tenant_id == feedback.tenant_id,
                    )
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
            if prior is not None:  # feedback_id is derived from the tenant id
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
                .where(
                    CanonicalMemoryRecord.id == memory.id,
                    CanonicalMemoryRecord.tenant_id == feedback.tenant_id,
                )
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
                    .where(
                        CanonicalMemoryRecord.id == memory_id,
                        CanonicalMemoryRecord.tenant_id == tenant_id,
                    )
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
