"""KB-03: a document cannot be deleted while it (or its collection) is under legal hold.

``DELETE /knowledge/collections/{id}`` checked ``is_under_hold`` but
``DELETE /knowledge/collections/{id}/documents/{doc}`` did not, so held data
could be deleted one document at a time.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.rag.store import KnowledgeStore
from tests.api.test_knowledge_extra4 import H, _create_collection, _make_app


def _client_with_hold(is_under_hold: Any) -> tuple[TestClient, str, AsyncMock]:
    store = KnowledgeStore()
    client = TestClient(_make_app(knowledge_store=store), raise_server_exceptions=False)
    collection_id = _create_collection(client)
    hold_mgr = AsyncMock()
    hold_mgr.is_under_hold = is_under_hold
    client.app.state.legal_hold_manager = hold_mgr  # type: ignore[attr-defined]
    spy = AsyncMock(return_value=3)
    store.delete_document_async = spy  # type: ignore[method-assign]
    return client, collection_id, spy


def test_held_document_delete_is_409_and_deletes_nothing() -> None:
    async def held(*, resource_id: str, tenant_id: str) -> bool:
        return resource_id == "doc-held"

    client, cid, spy = _client_with_hold(held)
    resp = client.delete(f"/knowledge/collections/{cid}/documents/doc-held", headers=H)
    assert resp.status_code == 409
    assert "legal hold" in resp.json()["detail"]
    spy.assert_not_awaited()


def test_document_in_a_held_collection_cannot_be_deleted() -> None:
    async def collection_held(*, resource_id: str, tenant_id: str) -> bool:
        return not resource_id.startswith("doc-")

    client, cid, spy = _client_with_hold(collection_held)
    resp = client.delete(f"/knowledge/collections/{cid}/documents/doc-1", headers=H)
    assert resp.status_code == 409
    spy.assert_not_awaited()


def test_unverifiable_hold_state_refuses_document_delete() -> None:
    client, cid, spy = _client_with_hold(AsyncMock(side_effect=RuntimeError("db down")))
    resp = client.delete(f"/knowledge/collections/{cid}/documents/doc-1", headers=H)
    assert resp.status_code == 503
    spy.assert_not_awaited()


def test_unheld_document_is_deleted() -> None:
    client, cid, spy = _client_with_hold(AsyncMock(return_value=False))
    resp = client.delete(f"/knowledge/collections/{cid}/documents/doc-1", headers=H)
    assert resp.status_code == 200, resp.text
    assert resp.json()["chunks_deleted"] == 3
    spy.assert_awaited_once()
