"""Integration test: a trigger firing replayed after the Redis TTL must not re-fire.

`TriggerDispatcher._is_duplicate` gated only on a Redis `SET NX` with a 60-second
TTL. That settles concurrent dispatch across replicas, but anything that
re-delivers the SAME firing later than the TTL — a consumer retry, a Celery
redelivery, a cron occurrence retried after a worker restart, a Redis failover
that drops keys — sailed straight through and created a second goal.

`trigger_events` already carries UNIQUE (tenant_id, idempotency_key) for exactly
this, but it was only written AFTER the goal was created, with
`ON CONFLICT DO NOTHING` and the result discarded — an audit row, not a gate.
So a replayed firing produced TWO goals and ONE audit row, which also makes the
duplicate invisible afterwards.

Run with:
    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \
    TESTCONTAINERS_RYUK_DISABLED=true \
        uv run pytest tests/triggers/test_trigger_replay_dedup.py -q -m integration
"""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

from app.triggers.dispatcher import TriggerDispatcher
from app.triggers.models import TriggerSpec, TriggerType

pytestmark = pytest.mark.integration

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TENANT = "tenant-trigger-replay"


class _ForgetfulRedis:
    """A Redis whose dedup key has always already expired.

    `set(..., nx=True)` always succeeds, which is precisely the post-TTL replay
    case (and equivalent to a failover that dropped the keyspace). The durable
    `trigger_events` row is the only thing that can catch the repeat.
    """

    async def set(self, *a: Any, **kw: Any) -> bool:
        return True

    async def get(self, *a: Any, **kw: Any) -> None:
        return None

    async def incr(self, *a: Any, **kw: Any) -> int:
        return 1

    async def expire(self, *a: Any, **kw: Any) -> bool:
        return True

    async def delete(self, *a: Any, **kw: Any) -> int:
        return 1

    async def eval(self, *a: Any, **kw: Any) -> int:
        return 1


class _FakeGoalService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_goal(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(goal_id=f"g-{len(self.calls)}")


@pytest.fixture(scope="module")
def postgres_url() -> Iterator[str]:
    with PostgresContainer("pgvector/pgvector:pg16", driver="asyncpg") as postgres:
        admin_url = postgres.get_connection_url()
        subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": admin_url},
            check=True,
            capture_output=True,
            text=True,
        )
        yield admin_url


@pytest_asyncio.fixture(scope="function")
async def factory(postgres_url: str) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(postgres_url)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as s:
        await s.execute(
            text("DELETE FROM trigger_events WHERE tenant_id = :t"), {"t": TENANT}
        )
        await s.commit()
    yield sf
    await engine.dispose()


def _spec() -> TriggerSpec:
    spec = TriggerSpec(
        trigger_type=TriggerType.REST,
        goal_template="Handle the inbound event",
    )
    # trigger_id is attached dynamically by the real callers
    # (app/triggers/store.py: `spec.trigger_id = sched_id`), not a dataclass field.
    spec.trigger_id = f"trg-{secrets.token_hex(4)}"  # type: ignore[attr-defined]
    return spec


@pytest.mark.asyncio
async def test_a_replayed_firing_does_not_create_a_second_goal(
    factory: async_sessionmaker,
) -> None:
    goal_service = _FakeGoalService()
    dispatcher = TriggerDispatcher(
        goal_service=goal_service,
        db_session_factory=factory,
        redis=_ForgetfulRedis(),
    )
    spec = _spec()
    ctx = SimpleNamespace(tenant_id=TENANT, plan="professional")
    payload = {"order_id": "A-1"}

    first = await dispatcher.dispatch(
        spec, payload, ctx, message_id="msg-1", txn_id="txn-1"
    )
    assert getattr(first, "goal_created", False) is True, first

    # Exactly the same firing, replayed after the Redis key expired.
    second = await dispatcher.dispatch(
        spec, payload, ctx, message_id="msg-1", txn_id="txn-1"
    )

    assert len(goal_service.calls) == 1, (
        f"replayed firing created a second goal: {goal_service.calls}"
    )
    assert getattr(second, "goal_created", True) is False, second
    assert getattr(second, "skip_reason", None) == "dedup", second


@pytest.mark.asyncio
async def test_a_genuinely_different_firing_still_fires(
    factory: async_sessionmaker,
) -> None:
    goal_service = _FakeGoalService()
    dispatcher = TriggerDispatcher(
        goal_service=goal_service,
        db_session_factory=factory,
        redis=_ForgetfulRedis(),
    )
    spec = _spec()
    ctx = SimpleNamespace(tenant_id=TENANT, plan="professional")

    await dispatcher.dispatch(spec, {"o": 1}, ctx, message_id="m-1", txn_id="t-1")
    await dispatcher.dispatch(spec, {"o": 2}, ctx, message_id="m-2", txn_id="t-2")
    assert len(goal_service.calls) == 2, goal_service.calls
