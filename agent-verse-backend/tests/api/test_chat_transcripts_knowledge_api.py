"""CHAT-KB: the two switches for chat transcripts as knowledge (owner decision 7).

* ``GET/PUT /tenants/me/chat-transcripts-knowledge`` — the tenant switch, off by
  default; only an admin changes it; turning it off removes every indexed
  transcript of the tenant.
* ``GET/PUT /chat/settings/knowledge`` — a person's own opt-in, off by default,
  refused while the tenant switch is off, revocable; revoking removes that
  person's indexed transcripts (held ones are kept and reported) and never
  another person's.
* A chat session records the person who created it, and a message the person
  who wrote it: only those can ever be indexed under that person's consent.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.tenants import router as tenants_router
from app.chat.router import router as chat_router
from app.rag.models import Chunk, KnowledgeCollection
from app.rag.store import KnowledgeStore
from app.services.chat_knowledge import KIND_CHAT_TRANSCRIPT
from app.tenancy.context import PlanTier, TenantContext

T = "tenant-chatkb-api"
_CALLERS = {
    "admin": TenantContext(T, PlanTier.PROFESSIONAL, "k1", roles=("admin",), user_id="user-adm"),
    "a": TenantContext(T, PlanTier.PROFESSIONAL, "k2", roles=("viewer",), user_id="user-a"),
    "b": TenantContext(T, PlanTier.PROFESSIONAL, "k3", roles=("viewer",), user_id="user-b"),
    "key": TenantContext(T, PlanTier.PROFESSIONAL, "k4", roles=("admin",)),
    "other": TenantContext("tenant-other", PlanTier.PROFESSIONAL, "k5", roles=("admin",),
                           user_id="user-a"),
}


def _app() -> FastAPI:
    app = FastAPI()
    app.state.db_session_factory = None
    app.state.knowledge_store = KnowledgeStore()

    @app.middleware("http")
    async def _caller(request: Any, call_next: Any) -> Any:
        who = request.headers.get("X-Test-Who")
        if who:
            request.state.tenant = _CALLERS[who]
        return await call_next(request)

    app.include_router(tenants_router)
    app.include_router(chat_router)
    return app


def _as(who: str) -> dict[str, str]:
    return {"X-Test-Who": who}


async def _seed(store: KnowledgeStore) -> str:
    ctx = TenantContext(T, PlanTier.FREE, "seed")
    cid = await store.create_collection_async(KnowledgeCollection(name="kb"), tenant_ctx=ctx)
    docs = {
        "ta-1": {"kind": KIND_CHAT_TRANSCRIPT, "user_id": "user-a", "chat_session_id": "s1"},
        "ta-2": {"kind": KIND_CHAT_TRANSCRIPT, "user_id": "user-a", "chat_session_id": "s2"},
        "tb-1": {"kind": KIND_CHAT_TRANSCRIPT, "user_id": "user-b", "chat_session_id": "s3"},
        "goal": {"kind": "goal_output", "goal_id": "g1"},
    }
    for doc_id, origin in docs.items():
        await store.ingest_chunks_async(
            [Chunk(document_id=doc_id, content=f"content {doc_id}", embedding=[0.1] * 8,
                   chunk_index=0, metadata={"origin": origin})],  # type: ignore[dict-item]
            collection_id=cid, tenant_ctx=ctx,
        )
    return cid


def _docs(store: KnowledgeStore, cid: str) -> set[str]:
    return {c.document_id for c in store._data[(T, cid)].chunks}


# ── tenant switch ────────────────────────────────────────────────────────────


def test_tenant_switch_is_off_by_default_and_admin_only() -> None:
    client = TestClient(_app())
    url = "/tenants/me/chat-transcripts-knowledge"
    assert client.get(url, headers=_as("a")).json() == {"enabled": False}
    refused = client.put(url, headers=_as("a"), json={"enabled": True})
    assert refused.status_code == 403
    assert client.get(url, headers=_as("a")).json() == {"enabled": False}
    on = client.put(url, headers=_as("admin"), json={"enabled": True})
    assert on.status_code == 200, on.text
    assert on.json()["enabled"] is True
    assert client.get(url, headers=_as("b")).json() == {"enabled": True}
    assert client.get(url, headers=_as("other")).json() == {"enabled": False}


async def test_turning_the_tenant_switch_off_removes_every_transcript() -> None:
    app = _app()
    client = TestClient(app)
    cid = await _seed(app.state.knowledge_store)
    url = "/tenants/me/chat-transcripts-knowledge"
    assert client.put(url, headers=_as("admin"), json={"enabled": True}).status_code == 200
    off = client.put(url, headers=_as("admin"), json={"enabled": False})
    assert off.status_code == 200, off.text
    assert off.json()["enabled"] is False
    assert off.json()["removed_documents"] == 3
    assert _docs(app.state.knowledge_store, cid) == {"goal"}


def test_an_unreadable_switch_is_503_not_off() -> None:
    app = _app()

    def _broken() -> Any:
        raise OSError("db down")

    app.state.db_session_factory = _broken
    client = TestClient(app)
    resp = client.get("/tenants/me/chat-transcripts-knowledge", headers=_as("a"))
    assert resp.status_code == 503


# ── per-user opt-in ──────────────────────────────────────────────────────────


def test_the_opt_in_belongs_to_a_person() -> None:
    client = TestClient(_app())
    assert client.get("/chat/settings/knowledge", headers=_as("key")).status_code == 403
    resp = client.put("/chat/settings/knowledge", headers=_as("key"), json={"opted_in": True})
    assert resp.status_code == 403


def test_opt_in_is_off_by_default_refused_while_the_tenant_switch_is_off() -> None:
    client = TestClient(_app())
    state = client.get("/chat/settings/knowledge", headers=_as("a")).json()
    assert state["opted_in"] is False and state["tenant_enabled"] is False
    refused = client.put("/chat/settings/knowledge", headers=_as("a"), json={"opted_in": True})
    assert refused.status_code == 409
    assert "admin" in refused.json()["detail"]
    client.put("/tenants/me/chat-transcripts-knowledge", headers=_as("admin"),
               json={"enabled": True})
    ok = client.put("/chat/settings/knowledge", headers=_as("a"), json={"opted_in": True})
    assert ok.status_code == 200, ok.text
    assert ok.json()["opted_in"] is True and ok.json()["opted_in_at"]
    # Per person: B did not opt in.
    assert client.get("/chat/settings/knowledge", headers=_as("b")).json()["opted_in"] is False
    assert client.get("/chat/settings/knowledge", headers=_as("a")).json()["opted_in"] is True


async def test_revoking_removes_only_my_transcripts() -> None:
    app = _app()
    client = TestClient(app)
    cid = await _seed(app.state.knowledge_store)
    client.put("/tenants/me/chat-transcripts-knowledge", headers=_as("admin"),
               json={"enabled": True})
    for who in ("a", "b"):
        client.put("/chat/settings/knowledge", headers=_as(who), json={"opted_in": True})
    resp = client.put("/chat/settings/knowledge", headers=_as("a"), json={"opted_in": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["opted_in"] is False and body["revoked_at"]
    assert body["removed_documents"] == 2 and body["held_documents"] == 0
    assert _docs(app.state.knowledge_store, cid) == {"tb-1", "goal"}


async def test_revoking_keeps_and_reports_held_transcripts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    client = TestClient(app)
    store: KnowledgeStore = app.state.knowledge_store
    cid = await _seed(store)

    async def _held(collection_id: str, ids: list[str], **_: Any) -> set[str]:
        return {i for i in ids if i == "ta-2"}

    monkeypatch.setattr(store, "held_document_ids_async", _held)
    resp = client.put("/chat/settings/knowledge", headers=_as("a"), json={"opted_in": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed_documents"] == 1
    assert resp.json()["held_documents"] == 1
    assert resp.json()["held_document_ids"] == ["ta-2"]
    assert _docs(store, cid) == {"ta-2", "tb-1", "goal"}


async def test_a_failed_removal_is_reported_not_faked(monkeypatch: pytest.MonkeyPatch) -> None:
    app = _app()
    client = TestClient(app)
    store: KnowledgeStore = app.state.knowledge_store
    await _seed(store)

    async def _broken(*_: Any, **__: Any) -> set[str]:
        raise OSError("hold table unavailable")

    monkeypatch.setattr(store, "held_document_ids_async", _broken)
    resp = client.put("/chat/settings/knowledge", headers=_as("a"), json={"opted_in": False})
    assert resp.status_code == 503
    assert "revoked" in resp.json()["detail"]
    # The consent itself is revoked: nothing more is indexed.
    assert client.get("/chat/settings/knowledge", headers=_as("a")).json()["opted_in"] is False


# ── ownership ────────────────────────────────────────────────────────────────


def test_a_session_records_the_person_who_created_it() -> None:
    client = TestClient(_app())
    mine = client.post("/chat/sessions", headers=_as("a"), json={"title": "mine"})
    assert mine.status_code == 201, mine.text
    assert mine.json()["owner_user_id"] == "user-a"
    keyed = client.post("/chat/sessions", headers=_as("key"), json={"title": "automation"})
    assert keyed.json()["owner_user_id"] is None


def test_a_message_records_the_person_who_wrote_it() -> None:
    client = TestClient(_app())
    sid = client.post("/chat/sessions", headers=_as("a"), json={"title": "x"}).json()["id"]
    sent = client.post(f"/chat/sessions/{sid}/messages", headers=_as("a"),
                       json={"content": "what is our refund policy?"})
    assert sent.status_code == 200, sent.text
    msgs = client.get(f"/chat/sessions/{sid}/messages", headers=_as("a")).json()["messages"]
    user_msgs = [m for m in msgs if m["role"] == "user"]
    assert user_msgs and user_msgs[0]["metadata"]["author_user_id"] == "user-a"
