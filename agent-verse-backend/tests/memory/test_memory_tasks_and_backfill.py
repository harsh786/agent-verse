from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.memory.backfill import LegacyMemoryRow, backfill_memory_rows
from app.memory.prospective import ProspectiveMemory, ProspectiveMemoryService, prospective_id
from app.memory.repository import InMemoryMemoryRepository
from app.scaling.memory_tasks import process_due_memories


@pytest.mark.asyncio
async def test_due_task_is_bounded_reauthorized_and_idempotent() -> None:
    service = ProspectiveMemoryService()
    now = datetime.now(UTC)
    for identifier in ("one", "two"):
        await service.create(
            ProspectiveMemory(
                memory_id=prospective_id("tenant", identifier),
                tenant_id="tenant",
                intention=identifier,
                due_at=now,
                expires_at=now + timedelta(hours=1),
                source_goal_id="goal",
                source_execution_id="execution",
                policy_snapshot={},
                classification="internal",
                idempotency_key=identifier,
            )
        )

    async def authorize(item: ProspectiveMemory) -> bool:
        return item.intention == "one"

    async def handler(item: ProspectiveMemory) -> dict[str, str]:
        return {"handled": item.intention}

    completed = await process_due_memories(
        service, tenant_id="tenant", now=now, authorize=authorize, handler=handler
    )
    assert [item.result for item in completed] == [{"handled": "one"}]
    assert await process_due_memories(
        service, tenant_id="tenant", now=now, authorize=authorize, handler=handler
    ) == ()


@pytest.mark.asyncio
async def test_backfill_checkpoints_batches_and_rerun_reuses_memory_ids() -> None:
    repository = InMemoryMemoryRepository()
    checkpoints = []

    async def save(marker) -> None:
        checkpoints.append(marker)

    rows = [
        LegacyMemoryRow(
            tenant_id="tenant",
            source_table="legacy",
            source_id=str(index),
            memory_kind="episodic",
            content=f"content {index}",
            source_goal_id="goal",
            source_execution_id="execution",
            evidence_refs=(f"evidence://{index}",),
        )
        for index in range(3)
    ]
    first = [
        item
        async for item in backfill_memory_rows(
            repository, rows, checkpoint=save, batch_size=2
        )
    ]
    second = [
        item
        async for item in backfill_memory_rows(
            repository, rows, checkpoint=save, batch_size=2
        )
    ]
    assert [item.source_id for item in first] == ["1", "2"]
    assert first == second
