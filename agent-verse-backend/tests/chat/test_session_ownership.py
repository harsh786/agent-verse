"""CHAT-SEC-1: a chat session is private to the principal that created it.

User B gets 404 on every endpoint for user A's session; A keeps full access; an
API key reaches only the sessions it created; sessions with no owner are only
reachable through the explicit, audited admin routes.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chat.ownership import SYSTEM_SCOPE, UNOWNED_SCOPE, ChatScope, principal_of
from app.chat.router import router as chat_router
from app.chat.service import ChatService
from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext

T = "tenant-chatsec"
_OPS = ("operator",)
_CALLERS = {
    "a": TenantContext(T, PlanTier.PROFESSIONAL, "user:user-a", roles=_OPS, user_id="user-a"),
    "b": TenantContext(T, PlanTier.PROFESSIONAL, "user:user-b", roles=_OPS, user_id="user-b"),
    "admin": TenantContext(T, PlanTier.PROFESSIONAL, "user:adm", roles=("admin",),
                           user_id="user-adm"),
    "key1": TenantContext(T, PlanTier.PROFESSIONAL, "key-1", roles=_OPS),
    "key2": TenantContext(T, PlanTier.PROFESSIONAL, "key-2", roles=_OPS),
    "nobody": TenantContext(T, PlanTier.PROFESSIONAL, "", roles=_OPS),
    "other": TenantContext("tenant-other", PlanTier.PROFESSIONAL, "user:user-a", roles=_OPS,
                           user_id="user-a"),
}


def _app(svc: ChatService | None = None) -> FastAPI:
    app = FastAPI()
    app.state.chat_service = svc or ChatService()
    app.state.audit_log = AuditLog()

    @app.middleware("http")
    async def _caller(request: Any, call_next: Any) -> Any:
        who = request.headers.get("X-Test-Who")
        if who:
            request.state.tenant = _CALLERS[who]
        return await call_next(request)

    app.include_router(chat_router)
    return app


def _as(who: str) -> dict[str, str]:
    return {"X-Test-Who": who}


def _seed(client: TestClient, who: str = "a") -> tuple[str, str]:
    sid = client.post("/chat/sessions", headers=_as(who), json={"title": "A secret"}).json()["id"]
    sent = client.post(
        f"/chat/sessions/{sid}/messages", headers=_as(who),
        json={"content": "my salary is 12345 and that is private"},
    )
    assert sent.status_code == 200, sent.text
    return sid, sent.json()["message_id"]


def _session_requests(sid: str, mid: str) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        ("GET", f"/chat/sessions/{sid}", {}),
        ("PATCH", f"/chat/sessions/{sid}", {"json": {"title": "pwned"}}),
        ("POST", f"/chat/sessions/{sid}/pin", {}),
        ("GET", f"/chat/sessions/{sid}/messages", {}),
        ("POST", f"/chat/sessions/{sid}/messages", {"json": {"content": "injected"}}),
        ("GET", f"/chat/sessions/{sid}/stream?message_id={mid}", {}),
        ("PATCH", f"/chat/sessions/{sid}/messages/{mid}", {"json": {"content": "edited"}}),
        ("POST", f"/chat/sessions/{sid}/messages/{mid}/feedback", {"json": {"rating": 1}}),
        ("GET", f"/chat/sessions/{sid}/usage", {}),
        ("POST", f"/chat/sessions/{sid}/summarize", {}),
        ("GET", f"/chat/sessions/{sid}/search?q=salary", {}),
        ("GET", f"/chat/sessions/{sid}/export", {}),
        ("POST", f"/chat/sessions/{sid}/move", {}),
        ("POST", f"/chat/sessions/{sid}/artifacts", {"json": {"title": "x", "content": "y"}}),
        ("GET", f"/chat/sessions/{sid}/artifacts", {}),
        ("PATCH", f"/chat/sessions/{sid}/artifacts/nope", {"json": {"content": "z"}}),
        ("DELETE", f"/chat/sessions/{sid}/artifacts/nope", {}),
        ("POST", f"/chat/sessions/{sid}/execute", {"json": {"code": "print(1)"}}),
        ("POST", f"/chat/sessions/{sid}/attachments",
         {"files": {"file": ("a.txt", b"hello", "text/plain")}}),
        ("DELETE", f"/chat/sessions/{sid}/messages/{mid}", {}),
        ("DELETE", f"/chat/sessions/{sid}", {}),
    ]


@pytest.mark.parametrize("intruder", ["b", "admin", "key1", "other", "nobody"])
def test_another_principal_gets_404_on_every_session_endpoint(intruder: str) -> None:
    app = _app()
    client = TestClient(app)
    sid, mid = _seed(client)
    for method, url, kwargs in _session_requests(sid, mid):
        resp = client.request(method, url, headers=_as(intruder), **kwargs)
        assert resp.status_code == 404, (intruder, method, url, resp.status_code, resp.text)
    # Nothing leaked into a list, a search, or the session itself.
    if intruder == "nobody":  # no principal: refused outright
        assert client.get("/chat/sessions", headers=_as(intruder)).status_code == 403
        assert client.post(
            "/chat/search", headers=_as(intruder), json={"query": "salary"}
        ).status_code == 403
        return
    listed = client.get("/chat/sessions", headers=_as(intruder)).json()["sessions"]
    assert sid not in {s["id"] for s in listed}
    found = client.post("/chat/search", headers=_as(intruder), json={"query": "salary"}).json()
    assert found["results"] == []
    scoped = client.post(
        "/chat/search", headers=_as(intruder), json={"query": "salary", "session_id": sid}
    ).json()
    assert scoped["results"] == []
    own = client.get(f"/chat/sessions/{sid}", headers=_as("a")).json()
    assert own["title"] == "A secret" and own["pinned"] is False
    msgs = client.get(f"/chat/sessions/{sid}/messages", headers=_as("a")).json()["messages"]
    assert [m["content"] for m in msgs] == ["my salary is 12345 and that is private"]


def test_the_owner_keeps_full_access() -> None:
    client = TestClient(_app())
    sid, mid = _seed(client)
    h = _as("a")
    assert client.get(f"/chat/sessions/{sid}", headers=h).status_code == 200
    assert client.patch(f"/chat/sessions/{sid}", headers=h, json={"title": "t2"}).json()[
        "title"
    ] == "t2"
    assert client.post(f"/chat/sessions/{sid}/pin", headers=h).json()["pinned"] is True
    assert client.get(f"/chat/sessions/{sid}/stream?message_id={mid}", headers=h).status_code == 200
    assert client.post(f"/chat/sessions/{sid}/summarize", headers=h).status_code == 200
    assert client.get(f"/chat/sessions/{sid}/export", headers=h).status_code == 200
    assert client.get(f"/chat/sessions/{sid}/usage", headers=h).status_code == 200
    assert client.get(f"/chat/sessions/{sid}/search?q=salary", headers=h).json()["total"] == 1
    assert client.post("/chat/search", headers=h, json={"query": "salary"}).json()["total"] == 1
    art = client.post(
        f"/chat/sessions/{sid}/artifacts", headers=h, json={"title": "x", "content": "y"}
    )
    assert art.status_code == 201
    listed = client.get("/chat/sessions", headers=h).json()
    assert [s["id"] for s in listed["sessions"]] == [sid]
    assert listed["principal"] == "user:user-a"
    assert listed["sessions"][0]["owner_principal"] == "user:user-a"
    assert client.delete(f"/chat/sessions/{sid}", headers=h).status_code == 204
    assert client.get(f"/chat/sessions/{sid}", headers=h).status_code == 404


def test_lists_hold_only_the_callers_own_sessions() -> None:
    client = TestClient(_app())
    a_sid, _ = _seed(client, "a")
    b_sid, _ = _seed(client, "b")
    k_sid, _ = _seed(client, "key1")
    assert [s["id"] for s in client.get("/chat/sessions", headers=_as("a")).json()["sessions"]] == [
        a_sid
    ]
    assert [s["id"] for s in client.get("/chat/sessions", headers=_as("b")).json()["sessions"]] == [
        b_sid
    ]
    assert [
        s["id"] for s in client.get("/chat/sessions", headers=_as("key1")).json()["sessions"]
    ] == [k_sid]
    # An admin's own list is their own sessions too: no silent cross-user read.
    assert client.get("/chat/sessions", headers=_as("admin")).json()["sessions"] == []


def test_api_key_reaches_only_sessions_it_created() -> None:
    client = TestClient(_app())
    k_sid, k_mid = _seed(client, "key1")
    a_sid, _ = _seed(client, "a")
    listed = client.get("/chat/sessions", headers=_as("key1")).json()
    assert listed["principal"] == "key:key-1"
    assert client.get(f"/chat/sessions/{k_sid}", headers=_as("key1")).status_code == 200
    # Another key and a person (even one in the same tenant) cannot reach it ...
    assert client.get(f"/chat/sessions/{k_sid}", headers=_as("key2")).status_code == 404
    assert client.get(f"/chat/sessions/{k_sid}", headers=_as("a")).status_code == 404
    # ... and the key cannot reach a person's session.
    assert client.get(f"/chat/sessions/{a_sid}", headers=_as("key1")).status_code == 404
    assert client.get(
        f"/chat/sessions/{k_sid}/messages", headers=_as("key1")
    ).json()["messages"][0]["id"] == k_mid


def test_a_caller_with_no_principal_cannot_create_or_list() -> None:
    client = TestClient(_app())
    refused = client.post("/chat/sessions", headers=_as("nobody"), json={})
    assert refused.status_code == 403
    assert client.get("/chat/sessions", headers=_as("nobody")).status_code == 403


def test_principal_of_and_scope_rules() -> None:
    assert principal_of(_CALLERS["a"]) == "user:user-a"
    assert principal_of(_CALLERS["key1"]) == "key:key-1"
    assert principal_of(_CALLERS["nobody"]) is None
    a = ChatScope.of("user:user-a")
    assert a.allows("user:user-a") and not a.allows("user:user-b") and not a.allows(None)
    assert UNOWNED_SCOPE.allows(None) and not UNOWNED_SCOPE.allows("user:user-a")
    assert SYSTEM_SCOPE.allows(None) and SYSTEM_SCOPE.allows("key:x")
    with pytest.raises(ValueError):
        ChatScope.of("")


# ── Sessions with no owner: admin only, explicit and audited ────────────────


def _unowned(svc: ChatService) -> str:
    s = svc.create_session(T, title="legacy")
    svc.save_message(s.id, T, "user", "an old unowned chat")
    return s.id


def test_unowned_sessions_are_admin_only_and_every_read_is_audited() -> None:
    svc = ChatService()
    app = _app(svc)
    client = TestClient(app)
    sid = _unowned(svc)
    for who in ("a", "b", "key1", "admin"):
        assert client.get(f"/chat/sessions/{sid}", headers=_as(who)).status_code == 404
        assert sid not in {
            s["id"] for s in client.get("/chat/sessions", headers=_as(who)).json()["sessions"]
        }
    # The admin routes refuse non-admins.
    assert client.get("/chat/admin/sessions/unowned", headers=_as("a")).status_code == 403
    assert client.get(f"/chat/admin/sessions/{sid}/messages", headers=_as("a")).status_code == 403
    listed = client.get("/chat/admin/sessions/unowned", headers=_as("admin")).json()
    assert [s["id"] for s in listed["sessions"]] == [sid]
    msgs = client.get(f"/chat/admin/sessions/{sid}/messages", headers=_as("admin")).json()
    assert [m["content"] for m in msgs["messages"]] == ["an old unowned chat"]
    audit: AuditLog = app.state.audit_log
    events = audit.query(tenant_ctx=_CALLERS["admin"], goal_id="chat.admin")
    assert {e.tool_name for e in events} == {"chat.admin.list_unowned", "chat.admin.read_unowned"}
    assert all(e.api_key_id == "user:adm" for e in events)
    assert any(sid in e.note for e in events)


def test_admin_cannot_read_an_owned_session_through_the_admin_routes() -> None:
    app = _app()
    client = TestClient(app)
    sid, _ = _seed(client, "a")
    assert sid not in {
        s["id"]
        for s in client.get("/chat/admin/sessions/unowned", headers=_as("admin")).json()[
            "sessions"
        ]
    }
    assert client.get(
        f"/chat/admin/sessions/{sid}/messages", headers=_as("admin")
    ).status_code == 404


def test_admin_assigns_an_unowned_session_to_a_person_audited() -> None:
    svc = ChatService()
    app = _app(svc)
    client = TestClient(app)
    sid = _unowned(svc)
    assigned = client.post(
        f"/chat/admin/sessions/{sid}/assign", headers=_as("admin"),
        json={"owner_user_id": "user-b"},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["owner_principal"] == "user:user-b"
    assert client.get(f"/chat/sessions/{sid}", headers=_as("b")).status_code == 200
    assert client.get(f"/chat/sessions/{sid}", headers=_as("a")).status_code == 404
    # Now owned, so it left the admin view and cannot be reassigned.
    assert client.get(
        f"/chat/admin/sessions/{sid}/messages", headers=_as("admin")
    ).status_code == 404
    again = client.post(
        f"/chat/admin/sessions/{sid}/assign", headers=_as("admin"),
        json={"owner_user_id": "user-a"},
    )
    assert again.status_code == 404
    audit: AuditLog = app.state.audit_log
    assert any(
        e.tool_name == "chat.admin.assign" and "user-b" in e.note
        for e in audit.query(tenant_ctx=_CALLERS["admin"])
    )


def test_admin_read_fails_closed_when_it_cannot_be_audited() -> None:
    svc = ChatService()
    app = _app(svc)

    class _Broken(AuditLog):
        async def record_durable(self, event: Any, *, tenant_ctx: Any) -> None:
            from app.governance.audit import AuditPersistenceError

            raise AuditPersistenceError("down")

    app.state.audit_log = _Broken()
    client = TestClient(app)
    sid = _unowned(svc)
    resp = client.get(f"/chat/admin/sessions/{sid}/messages", headers=_as("admin"))
    assert resp.status_code == 503
    assert "an old unowned chat" not in resp.text
