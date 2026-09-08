"""Tests for memory recall scoping (agent/collection/source) and TTL purge (D-18)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.memory.contracts import MemoryRecallRequest, MemoryRecord, MemoryWriteRequest
from app.memory.repository import InMemoryMemoryRepository
from app.scaling.memory_tasks import purge_expired_memories


def _write(**updates) -> MemoryWriteRequest:
    values = {
        "tenant_id": "tenant",
        "memory_kind": "reflexion",
        "content": "retry with strong evidence and context",
        "source_goal_id": "goal",
        "source_execution_id": "execution",
        "evidence_refs": ("evidence://1",),
        "classification": "internal",
        "confidence": 9000,
        "idempotency_key": "write",
        "retention_policy_id": "standard",
    }
    return MemoryWriteRequest(**{**values, **updates})


def _recall(**updates) -> MemoryRecallRequest:
    values = {
        "tenant_id": "tenant",
        "query": "evidence retry context",
        "memory_kinds": frozenset({"reflexion"}),
        "top_k": 10,
        "min_confidence": 0,
        "allowed_data_classes": frozenset({"internal"}),
        "as_of": datetime.now(UTC),
        "token_budget": 1000,
    }
    return MemoryRecallRequest(**{**values, **updates})


# --------------------------------------------------------------------------
# D-18(a): recall scoping
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recall_scoped_by_agent_id_excludes_other_agents() -> None:
    repo = InMemoryMemoryRepository()
    await repo.write(_write(idempotency_key="a", agent_id="agent-a"))
    await repo.write(_write(idempotency_key="b", agent_id="agent-b"))

    # No scoping → both visible (default behavior unchanged).
    unscoped = await repo.recall(_recall())
    assert len(unscoped) == 2

    scoped = await repo.recall(_recall(agent_id="agent-a"))
    assert len(scoped) == 1
    assert all(hit.record.agent_id == "agent-a" for hit in scoped)


@pytest.mark.asyncio
async def test_recall_scoped_by_collection_and_source() -> None:
    repo = InMemoryMemoryRepository()
    await repo.write(_write(idempotency_key="c1", collection_id="col-1", source="ingest"))
    await repo.write(_write(idempotency_key="c2", collection_id="col-2", source="manual"))

    by_collection = await repo.recall(_recall(collection_id="col-1"))
    assert [h.record.collection_id for h in by_collection] == ["col-1"]

    by_source = await repo.recall(_recall(source="manual"))
    assert [h.record.source for h in by_source] == ["manual"]


@pytest.mark.asyncio
async def test_recall_without_scope_is_backwards_compatible() -> None:
    repo = InMemoryMemoryRepository()
    await repo.write(_write(idempotency_key="x"))
    hits = await repo.recall(_recall())
    assert len(hits) == 1
    assert hits[0].record.agent_id is None


# --------------------------------------------------------------------------
# D-18(b): active retention purge
# --------------------------------------------------------------------------


def _record(
    memory_id: str, *, expires_at: datetime | None, tenant_id: str = "tenant"
) -> MemoryRecord:
    now = datetime.now(UTC)
    return MemoryRecord(
        memory_id=memory_id,
        tenant_id=tenant_id,
        memory_kind="reflexion",
        content_ref=f"memory://{memory_id}",
        safe_summary="a fact",
        source_goal_id="goal",
        source_execution_id="execution",
        evidence_refs=("evidence://1",),
        classification="internal",
        confidence=9000,
        lifecycle_state="active",
        version=1,
        embedding_model="memory-embedding-v1",
        embedding_dimension=1536,
        embedding=None,
        created_at=now,
        updated_at=now,
        expires_at=expires_at,
        retention_policy_id="standard",
        idempotency_key=memory_id,
    )


@pytest.mark.asyncio
async def test_purge_deletes_expired_and_keeps_unexpired() -> None:
    repo = InMemoryMemoryRepository()
    now = datetime.now(UTC)
    expired = _record("expired", expires_at=now - timedelta(hours=1))
    future = _record("future", expires_at=now + timedelta(days=1))
    never = _record("never", expires_at=None)
    for rec in (expired, future, never):
        repo._records[(rec.tenant_id, rec.memory_id)] = rec

    deleted = await purge_expired_memories(repo, tenant_id="tenant", now=now)

    assert deleted == 1
    remaining = {mid for (_t, mid) in repo._records}
    assert remaining == {"future", "never"}


@pytest.mark.asyncio
async def test_purge_is_tenant_scoped() -> None:
    repo = InMemoryMemoryRepository()
    now = datetime.now(UTC)
    mine = _record("mine", expires_at=now - timedelta(hours=1), tenant_id="tenant")
    theirs = _record("theirs", expires_at=now - timedelta(hours=1), tenant_id="other")
    for rec in (mine, theirs):
        repo._records[(rec.tenant_id, rec.memory_id)] = rec

    deleted = await purge_expired_memories(repo, tenant_id="tenant", now=now)

    assert deleted == 1
    # The other tenant's expired row is untouched by a tenant-scoped purge.
    assert ("other", "theirs") in repo._records
