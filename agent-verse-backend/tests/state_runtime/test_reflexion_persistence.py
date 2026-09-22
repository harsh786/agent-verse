# tests/state_runtime/test_reflexion_persistence.py
"""ReflexionStore must persist lessons to DB and survive restart."""
from __future__ import annotations

import asyncio

from app.state_runtime.reflexion_store import ReflexionStore


def test_reflexion_store_record_in_memory():
    """record() still works without DB (backward compat)."""
    store = ReflexionStore()
    store.record(tenant_id="t1", lesson="test lesson",
                 source_goal_id="g1", failure_class="auth")
    lessons = store.recall(tenant_id="t1", limit=5)
    assert len(lessons) == 1
    assert lessons[0]["lesson"] == "test lesson"


async def test_reflexion_store_record_async_no_db_does_not_raise():
    """record_async() without DB must complete silently."""
    store = ReflexionStore()
    await store.record_async(
        tenant_id="t1", lesson="async lesson",
        source_goal_id="g1", failure_class="timeout",
        db_factory=None,
    )
    lessons = store.recall(tenant_id="t1", limit=5)
    assert any(l["lesson"] == "async lesson" for l in lessons)


async def test_reflexion_store_record_async_writes_to_db():
    """record_async() must insert a row into reflexion_lessons table."""
    from unittest.mock import AsyncMock, MagicMock

    store = ReflexionStore()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock()

    mock_begin = AsyncMock()
    mock_begin.__aenter__ = AsyncMock(return_value=mock_begin)
    mock_begin.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_begin)

    db_factory = MagicMock(return_value=mock_session)

    await store.record_async(
        tenant_id="t1", lesson="DB lesson",
        source_goal_id="g1", failure_class="permission",
        db_factory=db_factory,
    )

    assert mock_session.execute.called


async def test_reflexion_store_load_from_db_seeds_memory():
    """load_from_db() seeds in-memory store from DB rows."""
    from unittest.mock import AsyncMock, MagicMock

    store = ReflexionStore()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.fetchall = MagicMock(return_value=[
        ["t1", "loaded lesson", "g_old", "auth_failure"]
    ])
    mock_session.execute = AsyncMock(return_value=mock_result)

    db_factory = MagicMock(return_value=mock_session)

    await store.load_from_db(tenant_id="t1", db_factory=db_factory)

    lessons = store.recall(tenant_id="t1", limit=10)
    assert any(l["lesson"] == "loaded lesson" for l in lessons)


def test_reflexion_store_evicts_oldest_lesson_past_max_per_tenant():
    """The deque is bounded per tenant; the oldest lesson is evicted first."""
    store = ReflexionStore(max_per_tenant=3)
    for i in range(5):
        store.record(tenant_id="t1", lesson=f"lesson-{i}", source_goal_id="g1", failure_class="auth")

    lessons = store.recall(tenant_id="t1", limit=10)
    assert [l["lesson"] for l in lessons] == ["lesson-2", "lesson-3", "lesson-4"]


async def test_reflexion_store_concurrent_record_async_calls_all_persist_in_memory():
    """Concurrent record_async() writers (real DB round trips) must not lose lessons."""
    from unittest.mock import AsyncMock, MagicMock

    store = ReflexionStore(max_per_tenant=50)

    def make_session() -> AsyncMock:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock()
        mock_begin = AsyncMock()
        mock_begin.__aenter__ = AsyncMock(return_value=mock_begin)
        mock_begin.__aexit__ = AsyncMock(return_value=False)
        mock_session.begin = MagicMock(return_value=mock_begin)
        return mock_session

    db_factory = MagicMock(side_effect=lambda: make_session())

    await asyncio.gather(
        *(
            store.record_async(
                tenant_id="t1",
                lesson=f"concurrent-{i}",
                source_goal_id="g1",
                failure_class="timeout",
                db_factory=db_factory,
            )
            for i in range(20)
        )
    )

    lessons = store.recall(tenant_id="t1", limit=50)
    assert {l["lesson"] for l in lessons} == {f"concurrent-{i}" for i in range(20)}


async def test_reflexion_store_record_async_db_failure_does_not_raise_and_keeps_memory_copy():
    """A DB error during persist must be swallowed; the in-memory lesson still lands."""
    from unittest.mock import MagicMock

    store = ReflexionStore()

    def _boom() -> None:
        raise RuntimeError("db unavailable")

    db_factory = MagicMock(side_effect=_boom)

    await store.record_async(
        tenant_id="t1",
        lesson="degrades gracefully",
        source_goal_id="g1",
        failure_class="db_outage",
        db_factory=db_factory,
    )

    lessons = store.recall(tenant_id="t1", limit=5)
    assert any(l["lesson"] == "degrades gracefully" for l in lessons)


async def test_reflexion_store_load_from_db_with_malformed_row_does_not_raise():
    """A row missing expected columns must not crash load_from_db (best-effort load)."""
    from unittest.mock import AsyncMock, MagicMock

    store = ReflexionStore()

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    # Malformed row: missing the failure_class column entirely.
    mock_result.fetchall = MagicMock(return_value=[["t1", "malformed lesson"]])
    mock_session.execute = AsyncMock(return_value=mock_result)

    db_factory = MagicMock(return_value=mock_session)

    await store.load_from_db(tenant_id="t1", db_factory=db_factory)

    # Best-effort: the malformed row is dropped silently, not raised.
    lessons = store.recall(tenant_id="t1", limit=10)
    assert lessons == []


def test_reflexion_store_recall_on_empty_tenant_without_db_factory_returns_empty_list():
    store = ReflexionStore()
    assert store.recall(tenant_id="unknown-tenant", limit=10) == []


async def test_reflexion_store_recall_triggers_lazy_hydration_when_event_loop_running():
    """recall() on an empty tenant with a db_factory wired schedules a fire-and-forget
    hydration (asyncio.ensure_future) when called from inside a running event loop —
    it does not block the caller, but the lesson does show up once that task runs."""
    from unittest.mock import AsyncMock, MagicMock

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.fetchall = MagicMock(
        return_value=[["t1", "lazy hydrated lesson", "g1", "auth"]]
    )
    mock_session.execute = AsyncMock(return_value=mock_result)
    db_factory = MagicMock(return_value=mock_session)

    store = ReflexionStore(db_factory=db_factory)

    # Nothing cached yet: the fire-and-forget task is scheduled but hasn't run.
    lessons = store.recall(tenant_id="t1", limit=5)
    assert lessons == []

    # Let the scheduled hydration task actually run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    lessons_after = store.recall(tenant_id="t1", limit=5)
    assert any(lesson["lesson"] == "lazy hydrated lesson" for lesson in lessons_after)


def test_reflexion_store_recall_respects_limit_and_returns_most_recent_last():
    store = ReflexionStore(max_per_tenant=10)
    for i in range(6):
        store.record(tenant_id="t1", lesson=f"lesson-{i}", source_goal_id="g1", failure_class="auth")

    lessons = store.recall(tenant_id="t1", limit=2)
    assert [l["lesson"] for l in lessons] == ["lesson-4", "lesson-5"]
