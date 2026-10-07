"""The process-wide kg_store is detached from the lifespan's DB pool at shutdown.

It used to keep the pool (bound to that lifespan's event loop), so any later user
in the same process — a second app, or tests after an app-booting package — ran
its queries on a closed loop ("attached to a different loop").
"""

import inspect

import app.main as main_mod


def test_lifespan_shutdown_detaches_kg_store() -> None:
    src = inspect.getsource(main_mod)
    shutdown = src[src.index("guardrails_repository_unbind_failed"):]
    shutdown = shutdown[: shutdown.index("await active.shutdown()")]
    assert "kg_store" in shutdown and "set_db(None)" in shutdown


def test_set_db_none_returns_to_in_memory_mode() -> None:
    from app.knowledge_graph.store import KnowledgeGraphStore

    store = KnowledgeGraphStore()
    store.set_db(object())
    store.set_db(None)
    assert store._db is None
