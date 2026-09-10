"""Uniform TTL: write() assigns expires_at for every kind, purge reclaims all kinds.

Regression guard for the memory TTL gap: previously ``write()`` never set
``expires_at``, so no record of any kind ever expired and the (kind-agnostic)
purge paths reclaimed nothing. These tests prove the retention deadline is now
assigned uniformly across episodic / procedural / reflexion (and that a permanent
policy is honoured), and that purge removes expired rows of every kind together.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.memory.contracts import MemoryWriteRequest
from app.memory.repository import InMemoryMemoryRepository
from app.memory.retention import resolve_expires_at
from app.scaling.memory_tasks import purge_expired_memories

KINDS = ("episodic", "procedural", "reflexion")


def _write(kind: str, *, policy: str, key: str, goal: str = "goal-1") -> MemoryWriteRequest:
    return MemoryWriteRequest(
        tenant_id="tenant",
        memory_kind=kind,  # type: ignore[arg-type]
        content=f"a durable {kind} lesson with evidence",
        source_goal_id=goal,
        source_execution_id="exec-1",
        evidence_refs=("evidence://1",),
        classification="internal",
        confidence=9000,
        idempotency_key=key,
        retention_policy_id=policy,
    )


@pytest.mark.asyncio
async def test_write_assigns_expiry_for_every_kind() -> None:
    """Each kind gets a finite, future expires_at under a finite retention policy."""
    repo = InMemoryMemoryRepository()
    for kind in KINDS:
        record = await repo.write(_write(kind, policy="default", key=f"{kind}-a"))
        assert record.expires_at is not None, f"{kind} record must carry a TTL"
        assert record.expires_at > record.created_at
        # Deadline is exactly what the retention policy resolves to.
        assert record.expires_at == resolve_expires_at("default", record.created_at)


@pytest.mark.asyncio
async def test_write_permanent_policy_never_expires() -> None:
    repo = InMemoryMemoryRepository()
    record = await repo.write(_write("reflexion", policy="permanent", key="perm"))
    assert record.expires_at is None


@pytest.mark.asyncio
async def test_purge_removes_all_kinds_uniformly() -> None:
    """Seed episodic + procedural + reflexion with elapsed TTLs → purge removes all."""
    repo = InMemoryMemoryRepository()
    for kind in KINDS:
        # ``ephemeral`` = 1 day TTL, so the record expires well before ``future``.
        await repo.write(_write(kind, policy="ephemeral", key=f"{kind}-ttl"))
    # A permanent record must survive the same purge.
    await repo.write(_write("reflexion", policy="permanent", key="keep"))

    future = datetime.now(UTC) + timedelta(days=2)
    deleted = await purge_expired_memories(repo, tenant_id="tenant", now=future)

    assert deleted == len(KINDS)
    survivors = await repo.list_records("tenant")
    assert {r.memory_kind for r in survivors} == {"reflexion"}
    assert all(r.expires_at is None for r in survivors)
