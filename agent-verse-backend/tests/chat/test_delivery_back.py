"""Phase 2 — delivery-back primitives: async goal/schedule results post into the
originating conversation."""

from __future__ import annotations

from app.chat.service import ChatService, extract_delivery_target


def test_extract_delivery_target_from_chat_binding() -> None:
    ec = {"source": "chat", "conversation_id": "s1", "session_id": "s1", "message_id": "m1"}
    assert extract_delivery_target(ec) == {"session_id": "s1", "message_id": "m1"}


def test_extract_delivery_target_ignores_non_chat() -> None:
    assert extract_delivery_target({"source": "api"}) is None
    assert extract_delivery_target({}) is None
    assert extract_delivery_target(None) is None
    # chat source but no session -> nothing to deliver to
    assert extract_delivery_target({"source": "chat"}) is None


def test_deliver_result_posts_followup_assistant_message() -> None:
    svc = ChatService()
    session = svc.create_session("t1", title="chat")
    msg = svc.deliver_result(
        session_id=session.id,
        tenant_id="t1",
        content="Your report is ready: 12 open bugs.",
        goal_id="g-9",
    )
    assert msg is not None
    assert msg.role == "assistant"
    assert msg.goal_id == "g-9"
    # persisted into the conversation history
    history = svc.list_messages(session.id, "t1")
    assert history[-1].content == "Your report is ready: 12 open bugs."


def test_deliver_result_to_missing_session_returns_none() -> None:
    svc = ChatService()
    assert svc.deliver_result(session_id="nope", tenant_id="t1", content="x") is None


async def test_adeliver_result_posts_and_reads_back_via_async_path() -> None:
    # Durable delivery-back path: writes and reads must go through the same
    # (a*) methods so a repo-backed deployment keeps them in one thread.
    svc = ChatService()
    session = await svc.acreate_session("t1", title="chat")
    msg = await svc.adeliver_result(
        session_id=session.id, tenant_id="t1", content="done", goal_id="g-1"
    )
    assert msg is not None and msg.role == "assistant" and msg.goal_id == "g-1"
    history = await svc.alist_messages(session.id, "t1")
    assert history[-1].content == "done"
    assert history[-1].metadata.get("delivery") == "async"


async def test_adeliver_result_to_missing_session_returns_none() -> None:
    svc = ChatService()
    assert await svc.adeliver_result(session_id="nope", tenant_id="t1", content="x") is None
