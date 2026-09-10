"""Tests for the A2A durable-intent repository (idempotency + status transitions).

Covers ``app.civilization.a2a_repository`` — previously at 0% coverage even
though it is the collaborator that makes internal A2A dispatch idempotent.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.civilization.a2a_repository import A2ATaskRecord, InMemoryA2ARepository

_DIGEST = "a" * 64  # valid 64-char lowercase-hex goal_digest


def _make_record(
    *,
    task_id: str = "task-1",
    tenant_id: str = "t1",
    idempotency_key: str = "idem-1",
    status: str = "pending",
) -> A2ATaskRecord:
    now = datetime.now(UTC)
    return A2ATaskRecord(
        task_id=task_id,
        tenant_id=tenant_id,
        civilization_id="civ-1",
        from_agent_id="agent-a",
        to_agent_id="agent-b",
        goal_digest=_DIGEST,
        context_reference=f"context://{task_id}",
        idempotency_key=idempotency_key,
        created_at=now,
        updated_at=now,
    )


# ── A2ATaskRecord validation ─────────────────────────────────────────────────


def test_record_rejects_bad_goal_digest():
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        A2ATaskRecord(
            task_id="t",
            tenant_id="t1",
            civilization_id="civ-1",
            from_agent_id="a",
            to_agent_id="b",
            goal_digest="XYZ",  # invalid — not 64-char lowercase hex
            context_reference="context://t",
            idempotency_key="k",
            created_at=now,
            updated_at=now,
        )


def test_record_is_frozen():
    record = _make_record()
    with pytest.raises(ValidationError):
        record.task_id = "mutated"  # frozen model


def test_record_defaults_to_pending():
    assert _make_record().status == "pending"
    assert _make_record().goal_id is None


# ── InMemoryA2ARepository.create (idempotency) ───────────────────────────────


@pytest.mark.asyncio
async def test_create_stores_and_returns_record():
    repo = InMemoryA2ARepository()
    record = _make_record()
    created = await repo.create(record)
    assert created is record
    assert await repo.get("t1", "task-1") is record


@pytest.mark.asyncio
async def test_create_is_idempotent_on_key():
    """A second create with the same (tenant, idempotency_key) returns the first record."""
    repo = InMemoryA2ARepository()
    first = _make_record(task_id="task-1", idempotency_key="dup")
    second = _make_record(task_id="task-2", idempotency_key="dup")  # different task_id, same key

    stored_first = await repo.create(first)
    stored_second = await repo.create(second)

    assert stored_first is first
    # Dedup: the second create returns the ORIGINAL record, not the new one.
    assert stored_second is first
    assert stored_second.task_id == "task-1"
    # The second task_id was never stored.
    assert await repo.get("t1", "task-2") is None


@pytest.mark.asyncio
async def test_idempotency_is_tenant_scoped():
    """The same idempotency_key under a different tenant is a distinct record."""
    repo = InMemoryA2ARepository()
    rec_t1 = _make_record(task_id="task-1", tenant_id="t1", idempotency_key="shared")
    rec_t2 = _make_record(task_id="task-2", tenant_id="t2", idempotency_key="shared")

    stored_t1 = await repo.create(rec_t1)
    stored_t2 = await repo.create(rec_t2)

    assert stored_t1.task_id == "task-1"
    assert stored_t2.task_id == "task-2"  # not deduped across tenants
    assert await repo.get("t2", "task-2") is rec_t2


# ── InMemoryA2ARepository.update ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_transitions_status_and_goal_id():
    repo = InMemoryA2ARepository()
    await repo.create(_make_record())
    updated = await repo.update("t1", "task-1", status="accepted", goal_id="goal-xyz")
    assert updated.status == "accepted"
    assert updated.goal_id == "goal-xyz"
    # Persisted back into the store.
    fetched = await repo.get("t1", "task-1")
    assert fetched is not None
    assert fetched.status == "accepted"
    assert fetched.goal_id == "goal-xyz"


@pytest.mark.asyncio
async def test_update_bumps_updated_at():
    repo = InMemoryA2ARepository()
    original = _make_record()
    await repo.create(original)
    updated = await repo.update("t1", "task-1", status="failed")
    assert updated.updated_at >= original.updated_at
    assert updated.created_at == original.created_at  # created_at preserved


@pytest.mark.asyncio
async def test_update_unknown_task_raises_keyerror():
    repo = InMemoryA2ARepository()
    with pytest.raises(KeyError):
        await repo.update("t1", "missing", status="accepted")


# ── InMemoryA2ARepository.get ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_missing_returns_none():
    repo = InMemoryA2ARepository()
    assert await repo.get("t1", "nope") is None
