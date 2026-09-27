# tests/persistence/test_startup_wiring.py
"""Startup wiring must be correct — no wildcard no-ops, lazy hydration works."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


def test_knowledge_graph_store_holds_no_graph_when_db_backed():
    """With a database wired, the store keeps no graph state in process.

    This replaced a per-tenant hydration TTL: each replica used to load the
    tenant's ENTIRE graph into dicts and re-load it every 30s so it would
    eventually see other replicas' writes. Reads now go to Postgres directly, so
    there is nothing to hydrate, nothing to go stale, and nothing to hold.
    """
    from app.knowledge_graph.models import GraphNode, NodeType
    from app.knowledge_graph.store import KnowledgeGraphStore

    store = KnowledgeGraphStore()
    store.add_node(GraphNode("pre", "t", NodeType.ENTITY, "dev-mode node"))
    store.set_db(MagicMock())
    assert store._nodes == {} and store._tenant_nodes == {}, (
        "wiring a database must drop dev-mode state so modes cannot mix"
    )
    assert not hasattr(store, "_hydrated_at")
    assert not hasattr(store, "load_from_db"), "whole-graph hydration must not exist"


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


def test_kg_sync_read_on_db_backed_store_refuses_instead_of_hydrating():
    """A sync read cannot consult Postgres, so it refuses rather than answering
    from an empty (or stale) in-process copy — and schedules no hydration."""
    import unittest.mock as _um

    import pytest

    from app.knowledge_graph.store import KnowledgeGraphStore, PersistedGraphRequiresAsyncError

    store = KnowledgeGraphStore()
    store.set_db(MagicMock())
    with (
        _um.patch("asyncio.ensure_future") as ensure_future,
        pytest.raises(PersistedGraphRequiresAsyncError),
    ):
        store.query_nodes(tenant_id="new_tenant_xyz")
    ensure_future.assert_not_called()


