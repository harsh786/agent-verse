"""CHAT-D-2: feedback on a chat reply is saved, idempotent per person, editable.

In-memory path through the router: the owner rates an assistant reply (thumbs
and a comment), rating again edits the same feedback, the message list carries
the caller's saved feedback, it can be cleared, and nobody else can rate or see
it (404). Feedback is for assistant replies only.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chat.router import router as chat_router
from app.chat.service import ChatService
from app.tenancy.context import PlanTier, TenantContext

T = "tenant-feedback"
_OPS = ("operator",)
_CALLERS = {
    "a": TenantContext(T, PlanTier.PROFESSIONAL, "user:user-a", roles=_OPS, user_id="user-a"),
    "b": TenantContext(T, PlanTier.PROFESSIONAL, "user:user-b", roles=_OPS, user_id="user-b"),
}


def _setup() -> tuple[TestClient, ChatService, str, str, str]:
    svc = ChatService()
    app = FastAPI()
    app.state.chat_service = svc

    @app.middleware("http")
    async def _caller(request: Any, call_next: Any) -> Any:
        request.state.tenant = _CALLERS[request.headers["X-Test-Who"]]
        return await call_next(request)

    app.include_router(chat_router)
    c = TestClient(app)
    sid = c.post("/chat/sessions", headers=_as("a"), json={}).json()["id"]
    user_mid = c.post(
        f"/chat/sessions/{sid}/messages", headers=_as("a"), json={"content": "what is 2+2?"}
    ).json()["message_id"]
    reply = svc.save_message(sid, T, "assistant", "4")
    return c, svc, sid, user_mid, reply.id


def _as(who: str) -> dict[str, str]:
    return {"X-Test-Who": who}


def _feedback_of(c: TestClient, sid: str, mid: str) -> Any:
    msgs = c.get(f"/chat/sessions/{sid}/messages", headers=_as("a")).json()["messages"]
    return next(m for m in msgs if m["id"] == mid)["feedback"]


def test_feedback_is_saved_and_shown_with_the_message() -> None:
    c, _svc, sid, user_mid, mid = _setup()
    assert _feedback_of(c, sid, mid) is None

    up = c.post(f"/chat/sessions/{sid}/messages/{mid}/feedback", headers=_as("a"),
                json={"rating": 1})
    assert up.status_code == 200, up.text
    assert (up.json()["rating"], up.json()["comment"]) == (1, None)
    assert _feedback_of(c, sid, mid)["rating"] == 1
    assert _feedback_of(c, sid, user_mid) is None


def test_rating_again_edits_the_same_feedback() -> None:
    c, svc, sid, _u, mid = _setup()
    url = f"/chat/sessions/{sid}/messages/{mid}/feedback"
    c.post(url, headers=_as("a"), json={"rating": 1})
    c.post(url, headers=_as("a"), json={"rating": 1})  # idempotent
    edited = c.post(url, headers=_as("a"), json={"rating": -1, "comment": "wrong total"})
    assert edited.status_code == 200
    saved = _feedback_of(c, sid, mid)
    assert (saved["rating"], saved["comment"]) == (-1, "wrong total")
    assert len(svc._feedback) == 1  # one feedback per person per message


def test_feedback_can_be_cleared() -> None:
    c, _svc, sid, _u, mid = _setup()
    url = f"/chat/sessions/{sid}/messages/{mid}/feedback"
    c.post(url, headers=_as("a"), json={"rating": 1})
    assert c.delete(url, headers=_as("a")).status_code == 204
    assert _feedback_of(c, sid, mid) is None
    assert c.delete(url, headers=_as("a")).status_code == 404


def test_feedback_is_for_assistant_replies_only() -> None:
    c, _svc, sid, user_mid, _mid = _setup()
    resp = c.post(f"/chat/sessions/{sid}/messages/{user_mid}/feedback", headers=_as("a"),
                  json={"rating": 1})
    assert resp.status_code == 422


def test_feedback_input_is_bounded() -> None:
    c, _svc, sid, _u, mid = _setup()
    url = f"/chat/sessions/{sid}/messages/{mid}/feedback"
    assert c.post(url, headers=_as("a"), json={"rating": 2}).status_code == 422
    assert c.post(url, headers=_as("a"),
                  json={"rating": 1, "comment": "x" * 4001}).status_code == 422


def test_another_person_can_neither_rate_nor_clear() -> None:
    c, svc, sid, _u, mid = _setup()
    url = f"/chat/sessions/{sid}/messages/{mid}/feedback"
    c.post(url, headers=_as("a"), json={"rating": 1})
    assert c.post(url, headers=_as("b"), json={"rating": -1}).status_code == 404
    assert c.delete(url, headers=_as("b")).status_code == 404
    assert _feedback_of(c, sid, mid)["rating"] == 1
    assert len(svc._feedback) == 1
