"""P7-1 against real Postgres: the AI-Ops run lease, its fenced save and the sweeper.

A dataset run is advanced by short worker steps; the run row's lease (a
compare-and-set on the DB clock) keeps two steps — on any replica — off the same
run, a step that lost its lease cannot overwrite progress, and the beat sweeper
re-dispatches only runs whose step chain died.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from typing import Any

import asyncpg
import pytest

from app.evals.ai_ops_store import AIOpsStore

pytestmark = pytest.mark.integration

_T = "t-ai-ops-lease"


def _plain(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


def _factory(url: str) -> Any:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    return async_sessionmaker(create_async_engine(url, poolclass=NullPool), expire_on_commit=False)


@pytest.fixture
def global_db(pg_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Point the process-global engines (the sweeper's system session) at pg_url."""
    from tests._test_backends import reset_db_singletons

    monkeypatch.setenv("DATABASE_URL", pg_url)
    monkeypatch.delenv("MAINTENANCE_DATABASE_URL", raising=False)
    reset_db_singletons()
    yield pg_url
    reset_db_singletons()


async def _add(store: AIOpsStore, payload: dict[str, Any]) -> str:
    rid = str(uuid.uuid4())
    await store.add_eval_result(
        tenant_id=_T, result_id=rid, dataset_id="d1", payload={"result_id": rid, **payload}
    )
    return rid


def test_lease_is_exclusive_fenced_and_released(pg_url: str) -> None:
    store = AIOpsStore(_factory(pg_url))

    async def _run() -> None:
        rid = await _add(store, {"status": "queued"})
        mine = await store.claim_run(_T, rid, "step-a", 60)
        assert mine is not None and mine["lease_owner"] == "step-a"
        assert await store.claim_run(_T, rid, "step-b", 60) is None
        # A step without the lease cannot write.
        assert await store.save_run(_T, rid, "step-b", {**mine, "status": "running"}, 60) is False
        mine["status"] = "running"
        mine["inflight"] = {"0": {"goal_id": "g0", "deadline": 1.0}}
        assert await store.save_run(_T, rid, "step-a", mine, 60) is True
        assert await store.claim_run(_T, rid, "step-b", 60) is None  # renewed
        assert await store.save_run(_T, rid, "step-a", mine, 60, release=True) is True
        theirs = await store.claim_run(_T, rid, "step-b", 60)
        assert theirs is not None and theirs["inflight"] == mine["inflight"]
        assert await store.save_run(_T, rid, "step-a", mine, 60) is False  # fenced out

        done = await _add(store, {"status": "completed"})
        assert await store.claim_run(_T, done, "step-a", 60) is None

        expired = await _add(store, {"status": "running", "lease_owner": "dead",
                                     "lease_until": 1.0})
        assert await store.claim_run(_T, expired, "step-c", 60) is not None

    asyncio.run(_run())


def test_sweeper_redispatches_only_stalled_runs(
    global_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.scaling import tasks

    store = AIOpsStore(_factory(global_db))
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(tasks.run_ai_ops_dataset, "apply_async", lambda **kw: sent.append(kw))

    async def _seed() -> tuple[str, str, str, str]:
        stalled = await _add(store, {"status": "running", "plan": "enterprise",
                                     "heartbeat_at": "2026-01-01T00:00:00+00:00"})
        leased = await _add(store, {"status": "running", "lease_owner": "alive",
                                    "lease_until": 9.9e12,
                                    "heartbeat_at": "2026-01-01T00:00:00+00:00"})
        fresh = await _add(store, {"status": "running", "heartbeat_at": "2999-01-01T00:00:00+00:00"})
        finished = await _add(store, {"status": "completed",
                                      "heartbeat_at": "2026-01-01T00:00:00+00:00"})
        return stalled, leased, fresh, finished

    async def _sweeps() -> tuple[str, dict[str, Any], dict[str, Any]]:
        import app.db.session as db_session

        stalled, _leased, _fresh, _finished = await _seed()
        try:
            first = await tasks._resume_stalled_ai_ops_runs_async(resume_after_seconds=60)
            second = await tasks._resume_stalled_ai_ops_runs_async(resume_after_seconds=60)
        finally:
            if db_session._engine is not None:
                await db_session._engine.dispose()
        return stalled, first, second

    stalled, first, second = asyncio.run(_sweeps())
    assert first == {"resumed_runs": 1}
    # The dispatch bumped the heartbeat: an immediate second sweep finds nothing.
    assert second == {"resumed_runs": 0}
    assert [s["kwargs"] for s in sent] == [
        {"tenant_id": _T, "plan": "enterprise", "result_id": stalled}
    ]

    async def _index() -> Any:
        conn = await asyncpg.connect(_plain(global_db))
        try:
            return await conn.fetchval(
                "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_ai_ops_eval_results_active'"
            )
        finally:
            await conn.close()

    assert "status" in json.dumps(asyncio.run(_index()))
