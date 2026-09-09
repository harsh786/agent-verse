# tests/state_runtime/test_reflexion_persistence.py
"""ReflexionStore must persist lessons to DB and survive restart."""
from __future__ import annotations

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
