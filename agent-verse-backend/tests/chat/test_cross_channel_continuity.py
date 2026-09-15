"""Phase 3 — cross-channel continuity: a thread continues across channels for one principal."""

from __future__ import annotations

from app.chat.service import ChatService
from app.identity import IdentityService


async def test_same_channel_user_continues_one_session() -> None:
    svc = ChatService()
    s1 = await svc.aget_or_create_channel_session(
        tenant_id="t1", channel="whatsapp", channel_user_id="+1"
    )
    s2 = await svc.aget_or_create_channel_session(
        tenant_id="t1", channel="whatsapp", channel_user_id="+1"
    )
    assert s1.id == s2.id  # same channel user → same thread


async def test_linked_identities_continue_same_session_across_channels() -> None:
    identity = IdentityService()
    svc = ChatService()
    svc.attach_engine(identity_service=identity)

    # Start on the web.
    web = await svc.ahandle_channel_message(
        tenant_id="t1", channel="web", channel_user_id="user-1", text="plan my launch"
    )
    web_session = web["session_id"]

    # Link the user's WhatsApp number to the same principal.
    web_principal = await identity.resolve_principal(
        tenant_id="t1", channel="web", channel_user_id="user-1"
    )
    await identity.link_identity(
        tenant_id="t1", principal_id=web_principal.id,
        channel="whatsapp", channel_user_id="+1",
    )

    # Next day, a WhatsApp message continues the SAME conversation.
    wa = await svc.ahandle_channel_message(
        tenant_id="t1", channel="whatsapp", channel_user_id="+1", text="add a press release step"
    )
    assert wa["session_id"] == web_session


async def test_unlinked_identities_are_separate_sessions() -> None:
    identity = IdentityService()
    svc = ChatService()
    svc.attach_engine(identity_service=identity)
    a = await svc.ahandle_channel_message(
        tenant_id="t1", channel="web", channel_user_id="user-1", text="hi"
    )
    b = await svc.ahandle_channel_message(
        tenant_id="t1", channel="whatsapp", channel_user_id="+unlinked", text="hi"
    )
    assert a["session_id"] != b["session_id"]
