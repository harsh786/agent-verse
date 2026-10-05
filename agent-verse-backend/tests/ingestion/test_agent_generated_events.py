"""P1e-1: platform events trigger syncs of the tenant's agent_generated Sources.

Nothing used to call the connector's ``on_webhook``: a completed goal, a decided
approval, a finished workflow run or a new lesson never reached the knowledge
base. Each now notifies the Sources listening for its kind; the notify task
queues their sync (or marks a running sync to run once more).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import fakeredis
import pytest

import app.ingestion.agent_generated_events as events
from app.ingestion.pipeline import chunk_origin

TENANT = "0f6c6c5a4bde4a49a4d4c1a3b7a1e001"


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    events._listeners.clear()


def _sources(*configs: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [(f"src-{i}", cfg) for i, cfg in enumerate(configs)]


@pytest.fixture
def fake_db(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"sources": [], "ready": True, "queries": 0, "fail": False}

    async def _rows(db_factory: Any, tenant_id: str, sql: str, params: dict[str, Any]) -> Any:
        state["queries"] += 1
        if state["fail"]:
            raise ConnectionRefusedError("db down")
        assert params["tid"] == tenant_id  # every read is tenant-scoped
        if "FROM source_configs" in sql:
            rows = state["sources"]
            if "id = :sid" in sql:
                rows = [r for r in rows if r[0] == params["sid"]]
            return rows if "SELECT id," in sql else [(cfg,) for _, cfg in rows]
        return [(1,)] if state["ready"] else []

    monkeypatch.setattr(events, "_rows", _rows)
    return state


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def _enqueue(tenant_id: str, kind: str = "", ref_id: str = "", **kw: Any) -> None:
        calls.append({"tenant_id": tenant_id, "kind": kind, "ref_id": ref_id, **kw})

    monkeypatch.setattr(events, "enqueue_notify", _enqueue)
    return calls


async def test_no_database_means_nothing_to_sync(fake_db: Any, enqueued: list[Any]) -> None:
    assert await events.notify_agent_generated(TENANT, "goal_output", "g1") is False
    assert fake_db["queries"] == 0 and enqueued == []


async def test_a_listening_source_is_notified(fake_db: Any, enqueued: list[Any]) -> None:
    fake_db["sources"] = _sources({"source_types": ["goal_output", "learning"]})
    assert await events.notify_agent_generated(TENANT, "goal_output", "g1", db_factory=object())
    assert enqueued == [{"tenant_id": TENANT, "kind": "goal_output", "ref_id": "g1"}]
    # A kind nobody listens for (defaults: goal_output + hitl_decision here) is not.
    assert not await events.notify_agent_generated(
        TENANT, "workflow_output", "r1", db_factory=object()
    )
    assert len(enqueued) == 1


async def test_listeners_are_cached_and_forgotten(fake_db: Any, enqueued: list[Any]) -> None:
    db = object()
    assert not await events.notify_agent_generated(TENANT, "goal_output", "g1", db_factory=db)
    queries = fake_db["queries"]
    fake_db["sources"] = _sources({})  # a Source is created (defaults listen for goals)
    assert not await events.notify_agent_generated(TENANT, "goal_output", "g2", db_factory=db)
    assert fake_db["queries"] == queries  # served from the cache
    events.forget_tenant(TENANT)  # what POST /sources does in this process
    assert await events.notify_agent_generated(TENANT, "goal_output", "g3", db_factory=db)


async def test_invalid_or_unknown_kinds_are_ignored(fake_db: Any, enqueued: list[Any]) -> None:
    fake_db["sources"] = _sources({"source_types": ["bogus"]}, {"source_types": ["learning"]})
    assert not await events.notify_agent_generated(TENANT, "chat", "x", db_factory=object())
    assert not await events.notify_agent_generated(TENANT, "goal_output", "x", db_factory=object())
    assert await events.notify_agent_generated(TENANT, "learning", "m1", db_factory=object())


async def test_notify_never_raises(fake_db: Any, enqueued: list[Any]) -> None:
    fake_db["fail"] = True
    assert not await events.notify_agent_generated(TENANT, "goal_output", "g1", db_factory=object())


async def test_notify_task_waits_for_the_record_then_queues_syncs(
    fake_db: Any, enqueued: list[Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.ingestion.scheduler as scheduler

    fake_db["sources"] = _sources({}, {"source_types": ["learning"]}, {})
    fake_db["ready"] = False
    out = await scheduler._agent_generated_notify_async(
        tenant_id=TENANT, kind="goal_output", ref_id="g1", source_id="", attempt=0
    )
    assert out == {"deferred": True, "attempt": 1}
    assert enqueued[-1]["attempt"] == 1 and enqueued[-1]["ref_id"] == "g1"

    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    monkeypatch.setattr(scheduler, "_reconcile_redis", lambda: redis)
    held = events.RERUN_KEY.format(tenant=TENANT, source="src-2")
    # src-2's sync is running: it holds the lock.
    from app.ingestion.job_tracker import IngestionJobTracker

    queued: list[dict[str, Any]] = []
    monkeypatch.setattr(
        scheduler.sync_source_task, "apply_async",
        lambda kwargs, queue: queued.append({"queue": queue, **kwargs}),
    )
    await redis.set(IngestionJobTracker._lock_key(TENANT, "src-2"), "other-run")
    fake_db["ready"] = True
    out = await scheduler._agent_generated_notify_async(
        tenant_id=TENANT, kind="goal_output", ref_id="g1", source_id="", attempt=1
    )
    assert out == {"queued": ["src-0"], "rerun": ["src-2"]}
    assert queued == [{
        "queue": "ingestion", "source_id": "src-0", "tenant_id": TENANT,
        "triggered_by": "event", "job_id": queued[0]["job_id"],
    }]
    assert await redis.get(held) == "1"

    # When the running sync ends it runs once more, for that Source only.
    await scheduler._rerun_if_events_arrived(redis, "src-2", TENANT)
    assert enqueued[-1] == {"tenant_id": TENANT, "kind": "", "ref_id": "",
                            "source_id": "src-2", "countdown": 1}
    assert await redis.get(held) is None
    before = len(enqueued)
    await scheduler._rerun_if_events_arrived(redis, "src-0", TENANT)
    assert len(enqueued) == before


def test_chunk_origin_is_a_small_flat_string_map() -> None:
    assert chunk_origin(None) == {}
    assert chunk_origin({"origin": "goal"}) == {}
    origin = chunk_origin({"origin": {
        "kind": "goal_output", "goal_id": "g1", "score": 0.9, "nested": {"x": 1},
        "empty": "", "long": "x" * 400,
    }})
    assert origin == {"kind": "goal_output", "goal_id": "g1", "score": "0.9", "long": "x" * 256}


async def test_goal_completion_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agent.state import GoalStatus
    from app.services.goal_service import GoalRecord, GoalService
    from app.tenancy.context import PlanTier, TenantContext

    calls: list[tuple[Any, ...]] = []

    async def _notify(tenant_id: str, kind: str, ref_id: str = "", *, db_factory: Any) -> bool:
        calls.append((tenant_id, kind, ref_id, db_factory))
        return True

    monkeypatch.setattr(events, "notify_agent_generated", _notify)
    ctx = TenantContext(tenant_id=TENANT, plan=PlanTier.FREE, api_key_id="k")
    db = object()
    svc = GoalService()
    svc._db = db
    for gid, dry in (("g1", False), ("g2", True)):
        svc._goals[gid] = GoalRecord(
            goal_id=gid, goal_text="g", status=GoalStatus.EXECUTING, tenant_id=TENANT,
            priority="normal", dry_run=dry, created_at=datetime.now(UTC).isoformat(),
            agent_id=None, execution_context={},
        )
        await svc._dispatch_event(gid, {"type": "goal_complete"}, tenant_ctx=ctx)
    assert calls == [(TENANT, "goal_output", "g1", db)]  # a dry run produces nothing
