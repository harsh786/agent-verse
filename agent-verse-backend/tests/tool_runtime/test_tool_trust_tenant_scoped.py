"""TOOLCTX-04: tool trust history is tenant-scoped and bounded in process memory.

``ToolTrustStore`` keyed its history by tool name only, so every tenant's
outcomes for a same-named tool (``search``) were mixed into one score in each
process, with no bound on the number of tools tracked. Postgres
``tool_trust_records`` (tenant-scoped, RLS) stays the durable source of truth;
the in-process store is now keyed by (tenant, tool) and LRU-bounded.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.orchestration_persistence import OrchestrationPersistence
from app.tool_runtime.tool_score import ToolScorer
from app.tool_runtime.tool_trust_store import ToolTrustStore


def test_history_is_per_tenant() -> None:
    store = ToolTrustStore()
    for _ in range(6):
        store.record_outcome("search", success=False, latency_ms=10, tenant_id="t-a")
    store.record_outcome("search", success=True, latency_ms=10, tenant_id="t-b")

    assert len(store.get_history("search", tenant_id="t-a")) == 6
    assert store.get_history("search", tenant_id="t-b") == [{"success": True, "latency_ms": 10}]
    scorer = ToolScorer(store)
    assert scorer.score("search", tenant_id="t-a").circuit_state == "open"
    assert scorer.score("search", tenant_id="t-b").circuit_state == "closed"


def test_tracked_keys_are_bounded() -> None:
    store = ToolTrustStore(max_keys=3)
    for i in range(5):
        store.record_outcome(f"tool{i}", success=True, latency_ms=1, tenant_id="t")
    assert not store.has_tool("tool0", tenant_id="t")
    assert store.has_tool("tool4", tenant_id="t")
    assert store.tracked_keys() == 3


@pytest.mark.asyncio
async def test_persistence_records_under_the_goal_tenant() -> None:
    store = ToolTrustStore()
    persistence = OrchestrationPersistence(tool_trust_store=store, reflexion_store=object())
    await persistence.persist_tool_outcome(
        "search", success=False, latency_ms=5, tenant_id="t-a", db=None
    )
    assert store.has_tool("search", tenant_id="t-a")
    assert not store.has_tool("search", tenant_id="t-b")


@pytest.mark.asyncio
async def test_load_from_db_seeds_only_that_tenant(monkeypatch: pytest.MonkeyPatch) -> None:
    store = ToolTrustStore()
    persistence = OrchestrationPersistence(tool_trust_store=store, reflexion_store=object())

    class _Result:
        def fetchall(self) -> list[tuple[Any, ...]]:
            return [("search", True, 3.0)]

    class _Session:
        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *a: Any) -> bool:
            return False

        def begin(self) -> _Session:
            return self

        async def execute(self, *a: Any, **k: Any) -> Any:
            return _Result()

    def _db() -> _Session:
        return _Session()

    class _Ctx:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *a: Any) -> bool:
            return False

    monkeypatch.setattr("app.db.rls.sqlalchemy_rls_context", lambda *a, **k: _Ctx())
    assert await persistence.load_tool_trust_from_db("t-a", db=_db) == 1
    assert store.has_tool("search", tenant_id="t-a")
    assert not store.has_tool("search", tenant_id="t-b")
