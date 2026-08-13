from __future__ import annotations

import pytest

from app.coordination.auction.repository import InMemoryAuctionRepository
from app.coordination.camel.repository import InMemoryCamelRepository
from app.coordination.generative.repository import InMemoryGenerativeRepository
from app.coordination.state_repository import PatternRecord
from app.coordination.store import OptimisticConflictError
from app.coordination.swarm.repository import InMemorySwarmRepository


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "repository_type",
    [
        InMemoryCamelRepository,
        InMemoryGenerativeRepository,
        InMemorySwarmRepository,
        InMemoryAuctionRepository,
    ],
)
async def test_program09_repositories_are_idempotent_optimistic_and_tenant_scoped(
    repository_type,
) -> None:
    repository = repository_type()
    record = PatternRecord(
        tenant_id="tenant",
        session_id="session",
        execution_id="execution",
        state={"phase": "active"},
        version=1,
        idempotency_key="create",
    )
    assert await repository.save(record, expected_version=0) == record
    assert await repository.save(record, expected_version=0) == record
    assert await repository.get("other", "session", "execution") is None
    with pytest.raises(OptimisticConflictError):
        await repository.save(
            record.model_copy(update={"version": 2, "idempotency_key": "stale"}),
            expected_version=0,
        )
