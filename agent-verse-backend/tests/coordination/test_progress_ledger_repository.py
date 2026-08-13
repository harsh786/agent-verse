from __future__ import annotations

import pytest

from app.coordination.ledger.models import LedgerRevision
from app.coordination.ledger.repository import InMemoryProgressLedgerRepository
from app.coordination.store import OptimisticConflictError


@pytest.mark.asyncio
async def test_ledger_append_is_immutable_idempotent_and_tenant_scoped() -> None:
    repository = InMemoryProgressLedgerRepository()
    first = await repository.append(
        LedgerRevision(
            tenant_id="tenant-a",
            session_id="session",
            version=1,
            objective="ship safely",
            open_work=("test",),
            idempotency_key="revision-1",
        ),
        expected_predecessor_version=0,
    )
    replay = await repository.append(first, expected_predecessor_version=0)
    second = await repository.append(
        first.model_copy(
            update={
                "version": 2,
                "completed_work": ("test",),
                "open_work": (),
                "idempotency_key": "revision-2",
            }
        ),
        expected_predecessor_version=1,
    )
    assert replay == first and second.version == 2
    assert await repository.current("tenant-a", "session") == second
    assert await repository.current("tenant-b", "session") is None
    assert [item.version for item in await repository.revisions("tenant-a", "session")] == [1, 2]
    with pytest.raises(OptimisticConflictError):
        await repository.append(
            second.model_copy(update={"version": 3, "idempotency_key": "stale"}),
            expected_predecessor_version=1,
        )
