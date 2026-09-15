"""Phase 3 — channel user -> durable session continuity."""

from __future__ import annotations

from app.chat.service import ChatService


def test_same_channel_user_reuses_session() -> None:
    svc = ChatService()
    s1 = svc.get_or_create_channel_session(tenant_id="t1", channel="whatsapp", channel_user_id="+15551234")
    s2 = svc.get_or_create_channel_session(tenant_id="t1", channel="whatsapp", channel_user_id="+15551234")
    assert s1.id == s2.id  # same conversation continues


def test_different_users_and_channels_are_separate() -> None:
    svc = ChatService()
    a = svc.get_or_create_channel_session(tenant_id="t1", channel="whatsapp", channel_user_id="+1")
    b = svc.get_or_create_channel_session(tenant_id="t1", channel="whatsapp", channel_user_id="+2")
    c = svc.get_or_create_channel_session(tenant_id="t1", channel="telegram", channel_user_id="+1")
    assert len({a.id, b.id, c.id}) == 3


def test_channel_session_is_tenant_scoped() -> None:
    svc = ChatService()
    a = svc.get_or_create_channel_session(tenant_id="t1", channel="whatsapp", channel_user_id="+1")
    b = svc.get_or_create_channel_session(tenant_id="t2", channel="whatsapp", channel_user_id="+1")
    assert a.id != b.id


def test_recreates_when_underlying_session_deleted() -> None:
    svc = ChatService()
    a = svc.get_or_create_channel_session(tenant_id="t1", channel="whatsapp", channel_user_id="+1")
    svc.delete_session(a.id, "t1")
    b = svc.get_or_create_channel_session(tenant_id="t1", channel="whatsapp", channel_user_id="+1")
    assert b.id != a.id and svc.get_session(b.id, "t1") is not None
