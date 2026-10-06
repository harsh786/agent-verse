"""CHAT-SEC-2: deleting a chat message really deletes it.

* The owner's DELETE removes the message from the store the API reads (in DB
  mode: the row; see test_message_delete_integration.py).
* Only the session's owner (404 for anyone else) or a tenant admin through the
  audited admin route can delete a message.
* The session's transcript leaves the knowledge index at once (a held document
  is kept and reported); when the removal cannot run, it is queued; when it
  cannot be queued either, the caller gets 503 (the message is already gone).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chat.router import router as chat_router
from app.chat.service import ChatService
from app.governance.audit import AuditLog
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.services.chat_knowledge import KIND_CHAT_TRANSCRIPT
from app.tenancy.context import PlanTier, TenantContext

T = "tenant-chatsec2"
_CALLERS = {
    "a": TenantContext(T, PlanTier.FREE, "user:user-a", roles=("viewer",), user_id="user-a"),
    "b": TenantContext(T, PlanTier.FREE, "user:user-b", roles=("viewer",), user_id="user-b"),
    "admin": TenantContext(T, PlanTier.FREE, "user:adm", roles=("admin",), user_id="adm"),
}


def _app() -> FastAPI:
    app = FastAPI()
    app.state.chat_service = ChatService()
    app.state.audit_log = AuditLog()
    app.state.knowledge_store = KnowledgeStore()
    app.state.db_session_factory = None

    @app.middleware("http")
    async def _caller(request: Any, call_next: Any) -> Any:
        request.state.tenant = _CALLERS[request.headers["X-Test-Who"]]
        return await call_next(request)

    app.include_router(chat_router)
    return app


def _h(who: str) -> dict[str, str]:
    return {"X-Test-Who": who}


def _seed(client: TestClient) -> tuple[str, str, str]:
    sid = client.post("/chat/sessions", headers=_h("a"), json={}).json()["id"]
    m1 = client.post(
        f"/chat/sessions/{sid}/messages", headers=_h("a"), json={"content": "my PIN is 4321"}
    ).json()["message_id"]
    m2 = client.post(
        f"/chat/sessions/{sid}/messages", headers=_h("a"), json={"content": "hello there"}
    ).json()["message_id"]
    return sid, m1, m2


async def _index(store: KnowledgeStore, sid: str) -> str:
    ctx = TenantContext(T, PlanTier.FREE, "seed")
    cid = await store.create_collection_async(KnowledgeCollection(name="kb"), tenant_ctx=ctx)
    for doc_id, session in (("transcript-a", sid), ("transcript-other", "other-session")):
        await store.ingest_chunks_async(
            [Chunk(document_id=doc_id, content="my PIN is 4321", embedding=[0.1] * 8,
                   chunk_index=0,
                   metadata={"origin": {"kind": KIND_CHAT_TRANSCRIPT, "user_id": "user-a",
                                        "chat_session_id": session}})],  # type: ignore[dict-item]
            collection_id=cid, tenant_ctx=ctx,
        )
    return cid


def _docs(store: KnowledgeStore, cid: str) -> set[str]:
    return {c.document_id for c in store._data[(T, cid)].chunks}


def _contents(client: TestClient, sid: str) -> list[str]:
    msgs = client.get(f"/chat/sessions/{sid}/messages", headers=_h("a")).json()["messages"]
    return [m["content"] for m in msgs]


async def test_owner_deletes_a_message_and_its_transcript_leaves_the_index() -> None:
    app = _app()
    client = TestClient(app)
    sid, m1, _m2 = _seed(client)
    cid = await _index(app.state.knowledge_store, sid)

    resp = client.delete(f"/chat/sessions/{sid}/messages/{m1}", headers=_h("a"))
    assert resp.status_code == 204, resp.text
    assert _contents(client, sid) == ["hello there"]
    assert client.get(
        f"/chat/sessions/{sid}/search?q=PIN", headers=_h("a")
    ).json()["total"] == 0
    # Only this session's transcript is removed; the next sync re-indexes it
    # without the deleted message.
    assert _docs(app.state.knowledge_store, cid) == {"transcript-other"}
    # Deleting it again: it no longer exists.
    assert client.delete(
        f"/chat/sessions/{sid}/messages/{m1}", headers=_h("a")
    ).status_code == 404


def test_another_person_cannot_delete_and_the_message_stays() -> None:
    client = TestClient(_app())
    sid, m1, _ = _seed(client)
    assert client.delete(f"/chat/sessions/{sid}/messages/{m1}", headers=_h("b")).status_code == 404
    # The ordinary route does not let an admin delete someone else's message either.
    assert client.delete(
        f"/chat/sessions/{sid}/messages/{m1}", headers=_h("admin")
    ).status_code == 404
    assert _contents(client, sid) == ["my PIN is 4321", "hello there"]


def test_a_message_id_of_another_session_is_not_deleted() -> None:
    client = TestClient(_app())
    sid, m1, _ = _seed(client)
    other = client.post("/chat/sessions", headers=_h("a"), json={}).json()["id"]
    assert client.delete(
        f"/chat/sessions/{other}/messages/{m1}", headers=_h("a")
    ).status_code == 404
    assert _contents(client, sid)[0] == "my PIN is 4321"


async def test_admin_deletes_a_message_through_the_audited_admin_route() -> None:
    app = _app()
    client = TestClient(app)
    sid, m1, _ = _seed(client)
    cid = await _index(app.state.knowledge_store, sid)
    assert client.delete(
        f"/chat/admin/sessions/{sid}/messages/{m1}", headers=_h("b")
    ).status_code == 403
    resp = client.delete(f"/chat/admin/sessions/{sid}/messages/{m1}", headers=_h("admin"))
    assert resp.status_code == 204, resp.text
    assert resp.content == b""  # nothing of the message is returned
    assert _contents(client, sid) == ["hello there"]
    assert _docs(app.state.knowledge_store, cid) == {"transcript-other"}
    audit: AuditLog = app.state.audit_log
    events = audit.query(tenant_ctx=_CALLERS["admin"], tool_name="chat.admin.delete_message")
    assert len(events) == 1 and m1 in events[0].note and sid in events[0].note
    assert client.delete(
        f"/chat/admin/sessions/{sid}/messages/{m1}", headers=_h("admin")
    ).status_code == 404


def test_admin_delete_fails_closed_when_it_cannot_be_audited() -> None:
    app = _app()

    class _Broken(AuditLog):
        async def record_durable(self, event: Any, *, tenant_ctx: Any) -> None:
            from app.governance.audit import AuditPersistenceError

            raise AuditPersistenceError("down")

    app.state.audit_log = _Broken()
    client = TestClient(app)
    sid, m1, _ = _seed(client)
    assert client.delete(
        f"/chat/admin/sessions/{sid}/messages/{m1}", headers=_h("admin")
    ).status_code == 503
    assert _contents(client, sid)[0] == "my PIN is 4321"


def test_index_removal_that_cannot_run_or_be_queued_is_reported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import chat_knowledge

    async def _down(*_a: Any, **_k: Any) -> Any:
        raise chat_knowledge.ChatKnowledgeUnavailableError("index down")

    queued: list[dict[str, Any]] = []

    def _queue(tenant_id: str, **kw: Any) -> None:
        queued.append({"tenant_id": tenant_id, **kw})

    monkeypatch.setattr(chat_knowledge, "remove_transcripts", _down)
    monkeypatch.setattr(chat_knowledge, "enqueue_purge_continuation", _queue)
    client = TestClient(_app())
    sid, m1, m2 = _seed(client)
    assert client.delete(f"/chat/sessions/{sid}/messages/{m1}", headers=_h("a")).status_code == 204
    assert queued == [{"tenant_id": T, "user_id": "user-a", "session_ids": [sid],
                       "unconsented_only": False}]

    def _refuse(*_a: Any, **_k: Any) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(chat_knowledge, "enqueue_purge_continuation", _refuse)
    resp = client.delete(f"/chat/sessions/{sid}/messages/{m2}", headers=_h("a"))
    assert resp.status_code == 503
    assert "deleted" in resp.json()["detail"]
    assert _contents(client, sid) == []
