"""Phase 0.3d — async repository-backed session CRUD (with in-memory fallback)."""

from __future__ import annotations

from datetime import UTC, datetime
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
