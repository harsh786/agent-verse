"""Gap 4 — the channel-user -> chat-session mapping must be durable.

The mapping used to live only in ``ChatService._channel_sessions`` (and the
principal -> session map in ``_principal_sessions``), so a restart or a second
replica forgot it: the next inbound Telegram/WhatsApp message from the same user
created a brand-new session and the conversation forked. When the service is
DB-backed (``repository`` wired), the mapping now lives in the repository
(``chat_channel_sessions`` / ``chat_principal_sessions`` tables) and every
ChatService instance sharing that store resolves the SAME session.

``_SharedRepo`` below is an in-memory stand-in for ``PostgresChatRepository``
whose calls genuinely suspend (like a DB round-trip), and whose
``resolve_channel_session`` mirrors the SQL contract: look up the mapping, else
insert a session + ``INSERT ... ON CONFLICT DO NOTHING`` the mapping and return
whichever session won.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.chat.service import ChatService
from app.identity import IdentityService


class _SharedRepo:
    def __init__(self) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.channel_map: dict[tuple[str, str, str], str] = {}
        self.principal_map: dict[tuple[str, str], str] = {}
        self.messages: list[dict[str, Any]] = []
        self.sessions_created = 0
        self._mutex = asyncio.Lock()  # stands in for the DB unique constraint

    async def create_session(self, *, session_id: str, tenant_id: str, **kw: Any) -> None:
        await asyncio.sleep(0.005)
        self.sessions_created += 1
        self.sessions[session_id] = {"id": session_id, "tenant_id": tenant_id, **kw}

    async def get_session(
        self, session_id: str, tenant_id: str, **_kw: Any
    ) -> dict[str, Any] | None:
        await asyncio.sleep(0)
        row = self.sessions.get(session_id)
        return dict(row) if row and row["tenant_id"] == tenant_id else None

    async def save_message(self, *, message_id: str, session_id: str, **kw: Any) -> None:
        await asyncio.sleep(0)
        self.messages.append({"id": message_id, "session_id": session_id, **kw})

    async def list_messages(self, session_id: str, tenant_id: str, **kw: Any) -> list[Any]:
        await asyncio.sleep(0)
        return [
            m for m in self.messages
            if m["session_id"] == session_id and m.get("tenant_id") == tenant_id
        ]

    async def resolve_channel_session(
        self,
        *,
        tenant_id: str,
        channel: str,
        channel_user_id: str,
        new_session_id: str,
        title: str,
    ) -> str:
        key = (tenant_id, channel, channel_user_id)
        await asyncio.sleep(0.005)
        async with self._mutex:
            existing = self.channel_map.get(key)
            if existing is not None:
                return existing
            self.sessions_created += 1
            self.sessions[new_session_id] = {
                "id": new_session_id, "tenant_id": tenant_id, "title": title,
            }
            self.channel_map[key] = new_session_id
            return new_session_id

    async def get_principal_session(self, tenant_id: str, principal_id: str) -> str | None:
        await asyncio.sleep(0)
        return self.principal_map.get((tenant_id, principal_id))

    async def claim_principal_session(
        self, *, tenant_id: str, principal_id: str, session_id: str
    ) -> str:
        await asyncio.sleep(0)
        return self.principal_map.setdefault((tenant_id, principal_id), session_id)


async def test_two_instances_sharing_the_store_resolve_the_same_session() -> None:
    repo = _SharedRepo()
    replica_a = ChatService(repository=repo)
    replica_b = ChatService(repository=repo)

    s_a = await replica_a.aget_or_create_channel_session(
        tenant_id="t1", channel="telegram", channel_user_id="chat-42"
    )
    s_b = await replica_b.aget_or_create_channel_session(
        tenant_id="t1", channel="telegram", channel_user_id="chat-42"
    )

    assert s_a.id == s_b.id
    assert repo.sessions_created == 1


async def test_restart_keeps_the_conversation() -> None:
    repo = _SharedRepo()
    before = await ChatService(repository=repo).achannel_turn(
        tenant_id="t1", channel="whatsapp", channel_user_id="+1555", text="hello"
    )
    # Process restart: a fresh ChatService with no in-memory state, same DB.
    after = await ChatService(repository=repo).achannel_turn(
        tenant_id="t1", channel="whatsapp", channel_user_id="+1555", text="still me"
    )
    assert after["session_id"] == before["session_id"]


async def test_replicas_racing_on_first_contact_converge_on_one_session() -> None:
    repo = _SharedRepo()
    replicas = [ChatService(repository=repo) for _ in range(4)]
    results = await asyncio.gather(
        *[
            r.aget_or_create_channel_session(
                tenant_id="t1", channel="telegram", channel_user_id="chat-7"
            )
            for r in replicas
            for _ in range(2)
        ]
    )
    assert len({s.id for s in results}) == 1
    # The one result is a real, readable session (no dangling id).
    assert await repo.get_session(results[0].id, "t1") is not None
    assert repo.sessions_created == 1


async def test_mapping_is_tenant_and_channel_scoped() -> None:
    repo = _SharedRepo()
    svc = ChatService(repository=repo)
    a = await svc.aget_or_create_channel_session(
        tenant_id="t1", channel="telegram", channel_user_id="u"
    )
    b = await svc.aget_or_create_channel_session(
        tenant_id="t2", channel="telegram", channel_user_id="u"
    )
    c = await svc.aget_or_create_channel_session(
        tenant_id="t1", channel="whatsapp", channel_user_id="u"
    )
    assert len({a.id, b.id, c.id}) == 3


async def test_store_error_fails_closed_instead_of_forking() -> None:
    class _Down(_SharedRepo):
        async def resolve_channel_session(self, **kw: Any) -> str:
            raise ConnectionError("db down")

    repo = _Down()
    svc = ChatService(repository=repo)
    with pytest.raises(ConnectionError):
        await svc.aget_or_create_channel_session(
            tenant_id="t1", channel="telegram", channel_user_id="chat-1"
        )
    assert repo.sessions_created == 0
    assert svc._sessions == {}  # no silent in-memory fallback session


async def test_mapping_to_a_missing_session_fails_closed() -> None:
    class _Dangling(_SharedRepo):
        async def resolve_channel_session(self, **kw: Any) -> str:
            return "ghost-session"

    svc = ChatService(repository=_Dangling())
    with pytest.raises(RuntimeError, match="missing chat session"):
        await svc.aget_or_create_channel_session(
            tenant_id="t1", channel="telegram", channel_user_id="chat-1"
        )


async def test_principal_thread_survives_restart() -> None:
    """Cross-channel continuity (linked identities) must also be durable."""
    repo = _SharedRepo()
    identity = IdentityService()

    first = ChatService(repository=repo)
    first.attach_engine(identity_service=identity)
    web = await first.aget_or_create_channel_session(
        tenant_id="t1", channel="web", channel_user_id="user-1"
    )
    principal = await identity.resolve_principal(
        tenant_id="t1", channel="web", channel_user_id="user-1"
    )
    await identity.link_identity(
        tenant_id="t1", principal_id=principal.id, channel="whatsapp", channel_user_id="+1"
    )

    restarted = ChatService(repository=repo)
    restarted.attach_engine(identity_service=identity)
    wa = await restarted.aget_or_create_channel_session(
        tenant_id="t1", channel="whatsapp", channel_user_id="+1"
    )
    assert wa.id == web.id


async def test_identity_error_is_not_swallowed() -> None:
    class _BrokenIdentity:
        async def resolve_principal(self, **kw: Any) -> Any:
            raise ConnectionError("identity store down")

    svc = ChatService(repository=_SharedRepo())
    svc.attach_engine(identity_service=_BrokenIdentity())
    with pytest.raises(ConnectionError):
        await svc.aget_or_create_channel_session(
            tenant_id="t1", channel="telegram", channel_user_id="chat-1"
        )


async def test_no_db_fallback_still_uses_the_in_memory_map() -> None:
    svc = ChatService()
    a = await svc.aget_or_create_channel_session(
        tenant_id="t1", channel="telegram", channel_user_id="chat-1"
    )
    b = await svc.aget_or_create_channel_session(
        tenant_id="t1", channel="telegram", channel_user_id="chat-1"
    )
    assert a.id == b.id
    assert svc._channel_sessions[("t1", "telegram", "chat-1")] == a.id
