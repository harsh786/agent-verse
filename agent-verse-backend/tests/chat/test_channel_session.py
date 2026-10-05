"""Phase 3 — channel user -> session continuity (no-DB fallback of the live async path).

The sync ``get_or_create_channel_session`` / ``handle_channel_message`` pair kept
the mapping in process memory and had no caller (CHAT-CHANNEL-DEAD); they were
removed. Inbound channels use ``aget_or_create_channel_session``, whose
repository path is covered by test_channel_session_durable.py and the
real-Postgres test_channel_sessions_integration.py.
"""

from __future__ import annotations

from app.chat.service import ChatService


async def test_same_channel_user_reuses_session() -> None:
    svc = ChatService()
    s1 = await svc.aget_or_create_channel_session(
        tenant_id="t1", channel="whatsapp", channel_user_id="+15551234"
    )
    s2 = await svc.aget_or_create_channel_session(
        tenant_id="t1", channel="whatsapp", channel_user_id="+15551234"
    )
    assert s1.id == s2.id  # same conversation continues


async def test_different_users_and_channels_are_separate() -> None:
    svc = ChatService()
    a = await svc.aget_or_create_channel_session(
        tenant_id="t1", channel="whatsapp", channel_user_id="+1"
    )
    b = await svc.aget_or_create_channel_session(
        tenant_id="t1", channel="whatsapp", channel_user_id="+2"
    )
    c = await svc.aget_or_create_channel_session(
        tenant_id="t1", channel="telegram", channel_user_id="+1"
    )
    assert len({a.id, b.id, c.id}) == 3


async def test_channel_session_is_tenant_scoped() -> None:
    svc = ChatService()
    a = await svc.aget_or_create_channel_session(
        tenant_id="t1", channel="whatsapp", channel_user_id="+1"
    )
    b = await svc.aget_or_create_channel_session(
        tenant_id="t2", channel="whatsapp", channel_user_id="+1"
    )
    assert a.id != b.id


async def test_recreates_when_underlying_session_deleted() -> None:
    svc = ChatService()
    a = await svc.aget_or_create_channel_session(
        tenant_id="t1", channel="whatsapp", channel_user_id="+1"
    )
    svc.delete_session(a.id, "t1")
    b = await svc.aget_or_create_channel_session(
        tenant_id="t1", channel="whatsapp", channel_user_id="+1"
    )
    assert b.id != a.id and svc.get_session(b.id, "t1") is not None
