"""Phase 0.3d — async repository-backed session CRUD (with in-memory fallback)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.chat.service import ChatService


class _FakeRepo:
    """Dict-returning fake matching PostgresChatRepository's session API."""

    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    async def create_session(self, *, session_id: str, tenant_id: str, title: str = "New Chat",
                             system_prompt: Any = None, agent_id: Any = None,
                             folder_id: Any = None) -> None:
        self._rows[session_id] = {
            "id": session_id, "tenant_id": tenant_id, "title": title,
            "system_prompt": system_prompt, "agent_id": agent_id, "folder_id": folder_id,
            "pinned": False, "created_at": datetime.now(UTC), "updated_at": datetime.now(UTC),
        }

    async def get_session(self, session_id: str, tenant_id: str) -> dict[str, Any] | None:
        r = self._rows.get(session_id)
        return r if r and r["tenant_id"] == tenant_id else None

    async def list_sessions(self, tenant_id: str) -> list[dict[str, Any]]:
        return [r for r in self._rows.values() if r["tenant_id"] == tenant_id]

    async def update_session(self, session_id: str, tenant_id: str, **fields: Any) -> bool:
        r = self._rows.get(session_id)
        if not r or r["tenant_id"] != tenant_id:
            return False
        r.update(fields)
        return True

    async def delete_session(self, session_id: str, tenant_id: str) -> bool:
        return self._rows.pop(session_id, None) is not None


async def test_async_crud_uses_repository_when_present() -> None:
    repo = _FakeRepo()
    svc = ChatService(repository=repo)
    s = await svc.acreate_session("t1", title="Durable", agent_id="a1")
    assert s.title == "Durable" and s.agent_id == "a1"
    # Persisted in the repo (survives a fresh in-memory service).
    svc2 = ChatService(repository=repo)
    got = await svc2.aget_session(s.id, "t1")
    assert got is not None and got.title == "Durable"
    # list / update / delete
    assert any(x.id == s.id for x in await svc2.alist_sessions("t1"))
    upd = await svc2.aupdate_session(s.id, "t1", title="Renamed")
    assert upd is not None and upd.title == "Renamed"
    assert await svc2.adelete_session(s.id, "t1") is True
    assert await svc2.aget_session(s.id, "t1") is None


async def test_async_crud_falls_back_to_memory_without_repo() -> None:
    svc = ChatService()  # no repository
    s = await svc.acreate_session("t1", title="Ephemeral")
    got = await svc.aget_session(s.id, "t1")
    assert got is not None and got.title == "Ephemeral"
    # same underlying in-memory store as the sync path
    assert svc.get_session(s.id, "t1") is not None


class _FakeRepoWithMessages(_FakeRepo):
    def __init__(self) -> None:
        super().__init__()
        self._msgs: list[dict[str, Any]] = []

    async def save_message(self, *, message_id: str, session_id: str, tenant_id: str,
                           role: str, content: str, intent: Any = None, goal_id: Any = None,
                           metadata: Any = None) -> None:
        # Monotonic timestamps so branch-prune's strict > ordering is deterministic.
        ts = datetime.now(UTC) + timedelta(microseconds=len(self._msgs))
        self._msgs.append({
            "id": message_id, "session_id": session_id, "tenant_id": tenant_id,
            "role": role, "content": content, "intent": intent, "goal_id": goal_id,
            "metadata": {}, "created_at": ts,
        })

    async def list_messages(self, session_id: str, tenant_id: str) -> list[dict[str, Any]]:
        return [m for m in self._msgs if m["session_id"] == session_id and m["tenant_id"] == tenant_id]

    async def get_message(self, message_id: str, tenant_id: str) -> dict[str, Any] | None:
        for m in self._msgs:
            if m["id"] == message_id and m["tenant_id"] == tenant_id:
                return m
        return None

    async def update_message_content(self, message_id: str, tenant_id: str, content: str) -> bool:
        for m in self._msgs:
            if m["id"] == message_id and m["tenant_id"] == tenant_id:
                m["content"] = content
                return True
        return False

    async def delete_messages_after(self, session_id: str, tenant_id: str,
                                    after_created_at: Any) -> list[str]:
        doomed = [
            m for m in self._msgs
            if m["session_id"] == session_id and m["tenant_id"] == tenant_id
            and m["created_at"] > after_created_at
        ]
        self._msgs = [m for m in self._msgs if m not in doomed]
        return [m["id"] for m in doomed]


async def test_async_message_crud_uses_repository() -> None:
    repo = _FakeRepoWithMessages()
    svc = ChatService(repository=repo)
    await svc.asave_message(session_id="s1", tenant_id="t1", role="user", content="hi", intent="QA")
    await svc.asave_message(session_id="s1", tenant_id="t1", role="assistant", content="hello")
    msgs = await svc.alist_messages("s1", "t1")
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content == "hi" and msgs[0].intent == "QA"
    # durable across a fresh service on the same repo
    svc2 = ChatService(repository=repo)
    assert len(await svc2.alist_messages("s1", "t1")) == 2


async def test_async_message_crud_falls_back_to_memory() -> None:
    svc = ChatService()
    session = svc.create_session("t1")
    await svc.asave_message(session_id=session.id, tenant_id="t1", role="user", content="hey")
    assert any(m.content == "hey" for m in await svc.alist_messages(session.id, "t1"))
    assert any(m.content == "hey" for m in svc.list_messages(session.id, "t1"))  # same store


async def test_aedit_message_edits_and_branch_prunes_via_repository() -> None:
    repo = _FakeRepoWithMessages()
    svc = ChatService(repository=repo)
    u1 = await svc.asave_message(session_id="s1", tenant_id="t1", role="user", content="q1")
    await svc.asave_message(session_id="s1", tenant_id="t1", role="assistant", content="a1")
    await svc.asave_message(session_id="s1", tenant_id="t1", role="user", content="q2")
    # Editing the first user message prunes everything after it.
    edited, pruned = await svc.aedit_message(u1.id, "t1", "q1-edited")
    assert edited is not None and edited.content == "q1-edited"
    assert len(pruned) == 2
    remaining = await svc.alist_messages("s1", "t1")
    assert [m.content for m in remaining] == ["q1-edited"]
    # Durable across a fresh service on the same repo.
    svc2 = ChatService(repository=repo)
    assert [m.content for m in await svc2.alist_messages("s1", "t1")] == ["q1-edited"]


async def test_aedit_message_rejects_non_user_and_missing() -> None:
    repo = _FakeRepoWithMessages()
    svc = ChatService(repository=repo)
    a1 = await svc.asave_message(session_id="s1", tenant_id="t1", role="assistant", content="a1")
    # assistant message is not editable
    assert await svc.aedit_message(a1.id, "t1", "x") == (None, [])
    # unknown id
    assert await svc.aedit_message("nope", "t1", "x") == (None, [])


async def test_aedit_message_falls_back_to_memory() -> None:
    svc = ChatService()
    session = svc.create_session("t1")
    u1 = svc.save_message(session_id=session.id, tenant_id="t1", role="user", content="q1")
    svc.save_message(session_id=session.id, tenant_id="t1", role="assistant", content="a1")
    edited, pruned = await svc.aedit_message(u1.id, "t1", "q1-edited")
    assert edited is not None and edited.content == "q1-edited"
    assert len(pruned) == 1
