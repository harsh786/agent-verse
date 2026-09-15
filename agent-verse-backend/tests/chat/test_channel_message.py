"""Phase 3 — inbound channel messages run the unified ChatService pipeline."""

from __future__ import annotations

from app.chat.service import ChatService


def test_channel_message_dispatches_with_session_and_intent() -> None:
    svc = ChatService()
    res = svc.handle_channel_message(
        tenant_id="t1", channel="whatsapp", channel_user_id="+1", text="what is 2+2?"
    )
    assert res["channel"] == "whatsapp"
    assert res["intent"] in {"QA", "GOAL", "CLARIFY", "SCHEDULE"}
    assert res["session_id"] and res["message_id"]


def test_channel_messages_accumulate_in_one_conversation() -> None:
    svc = ChatService()
    r1 = svc.handle_channel_message(
        tenant_id="t1", channel="telegram", channel_user_id="99", text="hi"
    )
    r2 = svc.handle_channel_message(
        tenant_id="t1", channel="telegram", channel_user_id="99", text="how are you?"
    )
    assert r1["session_id"] == r2["session_id"]  # same continuing thread
    history = svc.list_messages(r1["session_id"], "t1")
    assert [m.content for m in history if m.role == "user"] == ["hi", "how are you?"]
