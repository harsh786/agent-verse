"""KB-35: a persistent app whose legal-hold manager is not wired refuses deletes.

A missing ``legal_hold_manager`` silently allowed every knowledge delete. Only
the in-memory app (``manage_pools`` False: no durable data, no durable holds)
may skip the check.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.rag.store import KnowledgeStore
from tests.api.test_knowledge_extra4 import H, _create_collection, _make_app


def _client(*, manage_pools: bool) -> tuple[TestClient, str, AsyncMock]:
    store = KnowledgeStore()
    client = TestClient(_make_app(knowledge_store=store), raise_server_exceptions=False)
    cid = _create_collection(client)
    client.app.state.manage_pools = manage_pools  # type: ignore[attr-defined]
    client.app.state.legal_hold_manager = None  # type: ignore[attr-defined]
    spy = AsyncMock(return_value=True)
    store.delete_collection_async = spy  # type: ignore[method-assign]
    store.delete_document_async = spy  # type: ignore[method-assign]
    return client, cid, spy


def test_persistent_app_without_hold_manager_refuses_collection_delete() -> None:
    client, cid, spy = _client(manage_pools=True)
    resp = client.delete(f"/knowledge/collections/{cid}", headers=H)
    assert resp.status_code == 503
    spy.assert_not_awaited()


def test_persistent_app_without_hold_manager_refuses_document_delete() -> None:
    client, cid, spy = _client(manage_pools=True)
    resp = client.delete(f"/knowledge/collections/{cid}/documents/doc-1", headers=H)
    assert resp.status_code == 503
    spy.assert_not_awaited()


def test_in_memory_app_without_hold_manager_still_deletes() -> None:
    client, cid, spy = _client(manage_pools=False)
    resp = client.delete(f"/knowledge/collections/{cid}", headers=H)
    assert resp.status_code == 204
    spy.assert_awaited_once()


def test_persistent_app_without_hold_manager_refuses_reindex() -> None:
    from app.ingestion.job_tracker import IngestionJobTracker
    from tests.api.test_ingestion_ops_routes import _AUTH, _source
    from tests.api.test_ingestion_ops_routes import _client as ops_client

    source = _source()
    client = ops_client(ingestion_job_tracker=IngestionJobTracker(), manage_pools=True)
    with patch("app.ingestion.scheduler.sync_source_task") as task:
        resp = client.post(f"/sources/{source.source_id}/reindex", headers=_AUTH)
    assert resp.status_code == 503
    task.apply_async.assert_not_called()
