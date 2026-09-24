"""Phase 3 — cross-channel continuity: a thread continues across channels for one principal."""

from __future__ import annotations

import asyncio

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


class _FakeRacyRepository:
    """A fake PostgresChatRepository whose create/get calls actually suspend
    (like a real DB round-trip would), so concurrent callers genuinely
    interleave — reproducing the production race that a purely in-memory,
    never-suspending fake would hide."""

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}
        self.create_calls = 0

    async def create_session(
        self,
        *,
        session_id: str,
        tenant_id: str,
        title: str = "New Chat",
        system_prompt: str | None = None,
        agent_id: str | None = None,
        folder_id: str | None = None,
    ) -> None:
        self.create_calls += 1
        await asyncio.sleep(0.01)  # simulate DB latency — forces interleaving
        self._rows[session_id] = {
            "id": session_id,
            "tenant_id": tenant_id,
            "title": title,
            "system_prompt": system_prompt,
            "agent_id": agent_id,
            "folder_id": folder_id,
        }

    async def get_session(self, session_id: str, tenant_id: str) -> dict | None:
        await asyncio.sleep(0)
        row = self._rows.get(session_id)
        if row is None or row["tenant_id"] != tenant_id:
            return None
        return dict(row)


async def test_concurrent_channel_messages_do_not_fork_the_session() -> None:
    """Regression test: two inbound messages from the SAME external chat
    (e.g. a Telegram user double-sending, or two webhook deliveries racing)
    arriving concurrently must resolve to ONE durable session, not two.

    Before the per-key lock in aget_or_create_channel_session, both concurrent
    calls could miss the ``_channel_sessions`` cache (since the DB round-trip
    in acreate_session actually suspends), each create its own session, and
    the second write would clobber the map — silently forking the
    conversation and orphaning the first session from all future messages.
    """
    repo = _FakeRacyRepository()
    svc = ChatService(repository=repo)

    results = await asyncio.gather(
        *[
            svc.aget_or_create_channel_session(
                tenant_id="t1", channel="telegram", channel_user_id="chat-42"
            )
            for _ in range(5)
        ]
    )

    session_ids = {s.id for s in results}
    assert len(session_ids) == 1, f"expected one shared session, got {session_ids}"
    assert repo.create_calls == 1, (
        f"expected exactly one session to be created, got {repo.create_calls}"
    )
