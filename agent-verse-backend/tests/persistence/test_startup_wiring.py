# tests/persistence/test_startup_wiring.py
"""Startup wiring must be correct — no wildcard no-ops, lazy hydration works."""
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock


def test_knowledge_graph_store_has_hydrated_tenants_set():
    """KnowledgeGraphStore must track hydrated tenants for lazy load."""
    from app.knowledge_graph.store import KnowledgeGraphStore
    store = KnowledgeGraphStore()
    assert hasattr(store, "_hydrated_tenants"), "Missing _hydrated_tenants set"
    assert isinstance(store._hydrated_tenants, set)


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
    import asyncio

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
    assert "new_tenant_xyz" in store._hydrated_tenants
