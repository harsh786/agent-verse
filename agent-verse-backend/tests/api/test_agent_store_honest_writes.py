"""Regression: agent writes reported success they had not persisted.

* ``update_async`` mutated the replica cache first, then swallowed DB errors
  and returned True (PUT / rollback said "updated");
* ``delete_async`` fell back to an in-memory delete on a DB error and said
  "deleted" while the agent stayed active in Postgres;
* knowledge-binding routes used the sync cache-only ``update``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.api.agents import AgentStore
from app.tenancy.context import PlanTier, TenantContext

CTX = TenantContext(tenant_id="t-ag", plan=PlanTier.FREE, api_key_id="k")


class _BrokenDb:
    def __call__(self) -> Any:
        raise RuntimeError("db down")


@asynccontextmanager
async def _no_rls(session: Any, tenant_id: str) -> Any:
    yield session


async def test_update_db_failure_is_503_and_cache_is_untouched() -> None:
    store = AgentStore(db_session_factory=_BrokenDb())
    store._data[(CTX.tenant_id, "a1")] = {"agent_id": "a1", "name": "Original"}
    with pytest.raises(HTTPException) as exc:
        await store.update_async("a1", {"name": "Renamed"}, tenant_ctx=CTX)
    assert exc.value.status_code == 503
    assert store._data[(CTX.tenant_id, "a1")]["name"] == "Original"


async def test_delete_db_failure_is_503_and_agent_is_kept() -> None:
    store = AgentStore(db_session_factory=_BrokenDb())
    store._data[(CTX.tenant_id, "a1")] = {"agent_id": "a1", "name": "Keep"}
    with pytest.raises(HTTPException) as exc:
        await store.delete_async("a1", tenant_ctx=CTX)
    assert exc.value.status_code == 503
    assert (CTX.tenant_id, "a1") in store._data


async def test_knowledge_binding_persists_through_update_async() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from app.api import agents as agents_api

    store = MagicMock()
    store.update_async = AsyncMock(return_value=True)
    req = MagicMock()
    req.state = SimpleNamespace(tenant=CTX)
    with patch.object(agents_api, "_agent_store", return_value=store):
        out = await agents_api.update_knowledge_binding(
            req, "a1", agents_api.UpdateKnowledgeBindingRequest(collection_ids=["c1"])
        )
    assert out["allowed_collection_ids"] == ["c1"]
    store.update_async.assert_awaited_once()
    assert store.update_async.await_args.args[1] == {"allowed_collection_ids": ["c1"]}
