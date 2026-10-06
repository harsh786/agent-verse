"""CHAT-D-1: chat folders are the owner's own (in-memory path, through the router).

Folders are private like sessions: create, list, rename, delete and moving a
session into one are owner-only; another person sees none of them and gets 404;
a session can only be filed into a folder of the same owner.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chat.router import router as chat_router
from app.chat.service import ChatService
from app.tenancy.context import PlanTier, TenantContext

T = "tenant-folders"
_OPS = ("operator",)
_CALLERS = {
    "a": TenantContext(T, PlanTier.PROFESSIONAL, "user:user-a", roles=_OPS, user_id="user-a"),
    "b": TenantContext(T, PlanTier.PROFESSIONAL, "user:user-b", roles=_OPS, user_id="user-b"),
    "key1": TenantContext(T, PlanTier.PROFESSIONAL, "key-1", roles=_OPS),
    "nobody": TenantContext(T, PlanTier.PROFESSIONAL, "", roles=_OPS),
    "other": TenantContext("tenant-other", PlanTier.PROFESSIONAL, "user:user-a", roles=_OPS,
                           user_id="user-a"),
}


def _client(svc: ChatService | None = None) -> TestClient:
    app = FastAPI()
    app.state.chat_service = svc or ChatService()

    @app.middleware("http")
    async def _caller(request: Any, call_next: Any) -> Any:
        request.state.tenant = _CALLERS[request.headers["X-Test-Who"]]
        return await call_next(request)

    app.include_router(chat_router)
    return TestClient(app)


def _as(who: str) -> dict[str, str]:
    return {"X-Test-Who": who}


def test_folder_crud_is_owner_only() -> None:
    c = _client()
    made = c.post("/chat/folders", headers=_as("a"), json={"name": "Work", "color": "#ff0000"})
    assert made.status_code == 201, made.text
    fid = made.json()["id"]
    assert made.json()["name"] == "Work"

    assert [f["id"] for f in c.get("/chat/folders", headers=_as("a")).json()["folders"]] == [fid]
    for who in ("b", "key1", "other"):
        assert c.get("/chat/folders", headers=_as(who)).json()["folders"] == []
        assert c.patch(f"/chat/folders/{fid}", headers=_as(who),
                       json={"name": "pwned"}).status_code == 404
        assert c.delete(f"/chat/folders/{fid}", headers=_as(who)).status_code == 404
    assert c.get("/chat/folders", headers=_as("nobody")).status_code == 403

    renamed = c.patch(f"/chat/folders/{fid}", headers=_as("a"),
                      json={"name": "Clients", "color": "#00ff00"})
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()["name"], renamed.json()["color"]) == ("Clients", "#00ff00")

    assert c.delete(f"/chat/folders/{fid}", headers=_as("a")).status_code == 204
    assert c.get("/chat/folders", headers=_as("a")).json()["folders"] == []
    assert c.delete(f"/chat/folders/{fid}", headers=_as("a")).status_code == 404


def test_folder_color_must_be_a_hex_color() -> None:
    c = _client()
    bad = c.post("/chat/folders", headers=_as("a"),
                 json={"name": "X", "color": "red;background:url(x)"})
    assert bad.status_code == 422


def test_move_session_only_into_the_owners_own_folder() -> None:
    c = _client()
    fa = c.post("/chat/folders", headers=_as("a"), json={"name": "A's"}).json()["id"]
    fb = c.post("/chat/folders", headers=_as("b"), json={"name": "B's"}).json()["id"]
    sa = c.post("/chat/sessions", headers=_as("a"), json={}).json()["id"]
    sb = c.post("/chat/sessions", headers=_as("b"), json={}).json()["id"]

    # Another owner's folder does not exist for this caller.
    moved = c.post(f"/chat/sessions/{sb}/move", headers=_as("b"), params={"folder_id": fa})
    assert moved.status_code == 404
    assert moved.json()["detail"] == "Folder not found"
    assert c.patch(f"/chat/sessions/{sb}", headers=_as("b"),
                   json={"folder_id": fa}).status_code == 404
    assert c.post("/chat/sessions", headers=_as("b"),
                  json={"folder_id": fa}).status_code == 404
    # ... and another owner's session neither.
    assert c.post(f"/chat/sessions/{sa}/move", headers=_as("b"),
                  params={"folder_id": fb}).status_code == 404

    ok = c.post(f"/chat/sessions/{sa}/move", headers=_as("a"), params={"folder_id": fa})
    assert ok.status_code == 200, ok.text
    assert ok.json()["folder_id"] == fa
    out = c.post(f"/chat/sessions/{sa}/move", headers=_as("a"))
    assert out.status_code == 200 and out.json()["folder_id"] is None

    filed = c.post("/chat/sessions", headers=_as("a"), json={"folder_id": fa})
    assert filed.status_code == 201 and filed.json()["folder_id"] == fa


def test_deleting_a_folder_unfiles_its_sessions() -> None:
    c = _client()
    fid = c.post("/chat/folders", headers=_as("a"), json={"name": "Tmp"}).json()["id"]
    sid = c.post("/chat/sessions", headers=_as("a"), json={"folder_id": fid}).json()["id"]
    assert c.delete(f"/chat/folders/{fid}", headers=_as("a")).status_code == 204
    assert c.get(f"/chat/sessions/{sid}", headers=_as("a")).json()["folder_id"] is None
