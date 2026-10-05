"""Phase 3 — inbound channel messages run the unified (async) ChatService pipeline.

The sync ``handle_channel_message`` was dead (no caller, in-memory channel map)
and was removed (CHAT-CHANNEL-DEAD); these tests now cover the live
``ahandle_channel_message`` path the gateway and voice bridge use.
"""

from __future__ import annotations

from app.chat.service import ChatService


async def test_channel_message_dispatches_with_session_and_intent() -> None:
    svc = ChatService()
    res = await svc.ahandle_channel_message(
        tenant_id="t1", channel="whatsapp", channel_user_id="+1", text="what is 2+2?"
    )
    assert res["channel"] == "whatsapp"
    assert res["intent"] in {"QA", "GOAL", "CLARIFY", "SCHEDULE"}
    assert res["session_id"] and res["message_id"]


async def test_channel_messages_accumulate_in_one_conversation() -> None:
    svc = ChatService()
    r1 = await svc.ahandle_channel_message(
        tenant_id="t1", channel="telegram", channel_user_id="99", text="hi"
    )
    r2 = await svc.ahandle_channel_message(
        tenant_id="t1", channel="telegram", channel_user_id="99", text="how are you?"
    )
    assert r1["session_id"] == r2["session_id"]  # same continuing thread
    history = await svc.alist_messages(r1["session_id"], "t1")
    assert [m.content for m in history if m.role == "user"] == ["hi", "how are you?"]


def test_the_dead_sync_channel_entry_points_are_gone() -> None:
    """CHAT-CHANNEL-DEAD: no in-memory-only channel path to wire by mistake."""
    assert not hasattr(ChatService, "get_or_create_channel_session")
    assert not hasattr(ChatService, "handle_channel_message")
