# tests/persistence/test_startup_wiring.py
"""Startup wiring must be correct — no wildcard no-ops, lazy hydration works."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


def test_knowledge_graph_store_has_hydrated_at_ttl_map():
    """KnowledgeGraphStore must track per-tenant hydration recency (with a TTL),
    not a one-shot "seen it" set — a one-shot set means a replica that hydrates
    a tenant once never observes another replica's later writes/deletes for the
    rest of the process lifetime."""
    from app.knowledge_graph.store import KnowledgeGraphStore
    store = KnowledgeGraphStore()
    assert hasattr(store, "_hydrated_at"), "Missing _hydrated_at TTL map"
    assert isinstance(store._hydrated_at, dict)
    assert not hasattr(store, "_hydrated_tenants"), (
        "_hydrated_tenants (one-shot set) should have been replaced by the TTL map"
    )


def test_reflexion_store_accepts_db_factory():
    """ReflexionStore.__init__ must accept db_factory."""
    import inspect

    from app.state_runtime.reflexion_store import ReflexionStore
    sig = inspect.signature(ReflexionStore.__init__)
    assert "db_factory" in sig.parameters


def test_reflexion_store_has_hydrated_tenants_set():
    from app.state_runtime.reflexion_store import ReflexionStore
    store = ReflexionStore()
    assert hasattr(store, "_hydrated_tenants")


async def test_reflexion_store_recall_rehydrates_after_ttl_even_with_local_lessons():
    """recall() must not permanently stop looking at the DB just because this
    replica already has a local lesson for the tenant — otherwise a lesson
    another replica persisted via record_async() would never surface here.

    Run inside an async test (a running loop) to match how recall() is always
    actually invoked in production (from an async request/goal-loop handler).
    """
    import asyncio
    import unittest.mock as _um

    from app.state_runtime.reflexion_store import (
        ReflexionStore,
        _REHYDRATE_INTERVAL_SECONDS,
    )

    store = ReflexionStore(db_factory=MagicMock())
    store.record(
        tenant_id="t1", lesson="local lesson", source_goal_id="g1", failure_class="x"
    )

    with _um.patch.object(
        store, "load_from_db", new=AsyncMock(return_value=None)
    ) as mock_load:
        store.recall(tenant_id="t1")  # first call — always due (never hydrated)
        await asyncio.sleep(0)  # let the fire-and-forget task run
        assert mock_load.call_count == 1

        store.recall(tenant_id="t1")  # still within TTL — must not re-hydrate
        await asyncio.sleep(0)
        assert mock_load.call_count == 1

        store._hydrated_at["t1"] -= _REHYDRATE_INTERVAL_SECONDS + 1
        store.recall(tenant_id="t1")  # TTL elapsed — must re-hydrate
        await asyncio.sleep(0)
        assert mock_load.call_count == 2


def test_ab_testing_engine_accepts_db_factory():
    """ABTestingEngine.__init__ must accept db_factory."""
    import inspect

    from app.optimization.ab_testing import ABTestingEngine
    sig = inspect.signature(ABTestingEngine.__init__)
    assert "db_factory" in sig.parameters


async def test_orchestration_persistence_wildcard_loads_all():
    """load_tool_trust_from_db('*') must load all tenants, not return 0 rows."""
    from app.services.orchestration_persistence import OrchestrationPersistence
    from app.tool_runtime.tool_trust_store import ToolTrustStore

    store = ToolTrustStore()
    persist = OrchestrationPersistence(tool_trust_store=store)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_result = MagicMock()
    mock_result.fetchall = MagicMock(return_value=[
        ("jira.search", True, 200.0),
        ("github.list", False, 5000.0),
    ])
    mock_session.execute = AsyncMock(return_value=mock_result)
    db_factory = MagicMock(return_value=mock_session)

    await persist.load_tool_trust_from_db("*", db=db_factory)

    # Both tools must now be in the trust store
    history = store.get_history("jira.search")
    assert len(history) >= 1, "jira.search must be loaded from wildcard query"


def test_kg_lazy_hydration_triggers_on_first_miss():
    """query_nodes() for unknown tenant must schedule DB load."""

    from app.knowledge_graph.store import KnowledgeGraphStore

    store = KnowledgeGraphStore()
    store._db = MagicMock()  # simulate DB being wired

    tasks_scheduled = []

    def capture_future(coro, **kwargs):
        tasks_scheduled.append(coro)
        # Don't actually run it in unit test
        try:
            coro.close()
        except Exception:
            pass
        return MagicMock()

    import unittest.mock as _um
    with _um.patch("asyncio.ensure_future", side_effect=capture_future):
        result = store.query_nodes(tenant_id="new_tenant_xyz")

    assert len(tasks_scheduled) > 0, "No async task scheduled for lazy hydration"
    assert "new_tenant_xyz" in store._hydrated_at


def test_kg_lazy_hydration_refreshes_after_ttl():
    """A second query_nodes() call after the TTL window must re-hydrate — this is
    what lets a replica observe another replica's writes/deletes without a
    restart. Immediately re-querying inside the TTL window must NOT re-hydrate
    (that would defeat the point of caching / thunder the DB on every call)."""
    import unittest.mock as _um

    from app.knowledge_graph.store import KnowledgeGraphStore, _REHYDRATE_INTERVAL_SECONDS

    store = KnowledgeGraphStore()
    store._db = MagicMock()

    tasks_scheduled = []

    def capture_future(coro, **kwargs):
        tasks_scheduled.append(coro)
        try:
            coro.close()
        except Exception:
            pass
        return MagicMock()

    with _um.patch("asyncio.ensure_future", side_effect=capture_future):
        store.query_nodes(tenant_id="replica_test_tenant")
        assert len(tasks_scheduled) == 1

        # Still fresh — must not re-hydrate.
        store.query_nodes(tenant_id="replica_test_tenant")
        assert len(tasks_scheduled) == 1

        # Simulate the TTL having elapsed (another replica may have written or
        # deleted data for this tenant in the meantime).
        store._hydrated_at["replica_test_tenant"] -= _REHYDRATE_INTERVAL_SECONDS + 1
        store.query_nodes(tenant_id="replica_test_tenant")
        assert len(tasks_scheduled) == 2, "Did not re-hydrate after the TTL elapsed"
