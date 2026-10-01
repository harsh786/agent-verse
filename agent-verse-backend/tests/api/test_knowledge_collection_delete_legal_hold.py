"""KB-33: DELETE /knowledge/collections/{id} honours document-level legal holds.

Only the collection id was checked, so a hold on one of its documents was
bypassed by deleting the whole collection.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.rag.store import KnowledgeLegalHoldError, KnowledgeStore
from tests.api.test_knowledge_extra4 import H, _create_collection, _make_app


def _client(store: KnowledgeStore) -> tuple[TestClient, str]:
    client = TestClient(_make_app(knowledge_store=store), raise_server_exceptions=False)
    cid = _create_collection(client)
    hold_mgr = AsyncMock()
    hold_mgr.is_under_hold = AsyncMock(return_value=False)  # collection itself not held
    client.app.state.legal_hold_manager = hold_mgr  # type: ignore[attr-defined]
    return client, cid


def test_held_document_in_collection_gives_409_and_deletes_nothing() -> None:
    store = KnowledgeStore()
    client, cid = _client(store)
    store.collection_under_legal_hold_async = AsyncMock(return_value=True)  # type: ignore[method-assign]
    spy = AsyncMock(return_value=True)
    store.delete_collection_async = spy  # type: ignore[method-assign]
    resp = client.delete(f"/knowledge/collections/{cid}", headers=H)
    assert resp.status_code == 409
    assert "legal hold" in resp.json()["detail"]
    spy.assert_not_awaited()


def test_unverifiable_document_holds_refuse_the_delete() -> None:
    store = KnowledgeStore()
    client, cid = _client(store)
    store.collection_under_legal_hold_async = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
    spy = AsyncMock(return_value=True)
    store.delete_collection_async = spy  # type: ignore[method-assign]
    resp = client.delete(f"/knowledge/collections/{cid}", headers=H)
    assert resp.status_code == 503
    spy.assert_not_awaited()


def test_hold_appearing_mid_delete_gives_409() -> None:
    store = KnowledgeStore()
    client, cid = _client(store)
    store.collection_under_legal_hold_async = AsyncMock(return_value=False)  # type: ignore[method-assign]
    store.delete_collection_async = AsyncMock(  # type: ignore[method-assign]
        side_effect=KnowledgeLegalHoldError("held")
    )
    resp = client.delete(f"/knowledge/collections/{cid}", headers=H)
    assert resp.status_code == 409


def test_unheld_collection_is_deleted() -> None:
    store = KnowledgeStore()
    client, cid = _client(store)
    resp = client.delete(f"/knowledge/collections/{cid}", headers=H)
    assert resp.status_code == 204
