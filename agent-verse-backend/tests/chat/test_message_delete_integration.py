"""CHAT-SEC-2 on a real Postgres (NOBYPASSRLS app role): a deleted message's
row leaves ``chat_messages``; another person's delete leaves it untouched; the
admin route deletes too; the session's ``updated_at`` moves so its transcript is
re-read by the next sync.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/chat/test_message_delete_integration.py -m integration
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.chat.repository import PostgresChatRepository
from app.chat.router import router as chat_router
from app.chat.service import ChatService
from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = pytest.mark.integration


async def test_deleted_message_row_is_gone(pg_url: str) -> None:
    from fastapi import FastAPI

    t = "t-" + uuid.uuid4().hex[:10]
    await seed_tenant(pg_url, t)
    callers = {
        "a": TenantContext(t, PlanTier.FREE, "user:a", roles=("viewer",), user_id="user-a"),
        "b": TenantContext(t, PlanTier.FREE, "user:b", roles=("viewer",), user_id="user-b"),
        "admin": TenantContext(t, PlanTier.FREE, "user:adm", roles=("admin",), user_id="adm"),
    }
    engine = await app_engine(pg_url)
    app = FastAPI()
    app.state.chat_service = ChatService(repository=PostgresChatRepository(sessions(engine)))
    app.state.audit_log = AuditLog()

    @app.middleware("http")
    async def _caller(request: Any, call_next: Any) -> Any:
        request.state.tenant = callers[request.headers["X-Test-Who"]]
        return await call_next(request)

    app.include_router(chat_router)

    async def rows(sid: str) -> list[str]:
        found = await admin_exec(
            pg_url, "SELECT content FROM chat_messages WHERE session_id = :s ORDER BY created_at",
            {"s": sid},
        )
        return [str(r[0]) for r in found]

    async def touched(sid: str) -> Any:
        return (
            await admin_exec(pg_url, "SELECT updated_at FROM chat_sessions WHERE id = :s",
                             {"s": sid})
        )[0][0]

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://x") as c:
            h = {"X-Test-Who": "a"}
            sid = (await c.post("/chat/sessions", headers=h, json={})).json()["id"]
            ids = []
            for text_ in ("secret one", "keep me", "secret two"):
                r = await c.post(f"/chat/sessions/{sid}/messages", headers=h,
                                 json={"content": text_})
                ids.append(r.json()["message_id"])
            assert await rows(sid) == ["secret one", "keep me", "secret two"]

            # B cannot delete it, and nothing changes.
            r = await c.delete(f"/chat/sessions/{sid}/messages/{ids[0]}",
                               headers={"X-Test-Who": "b"})
            assert r.status_code == 404
            assert await rows(sid) == ["secret one", "keep me", "secret two"]

            before = await touched(sid)
            r = await c.delete(f"/chat/sessions/{sid}/messages/{ids[0]}", headers=h)
            assert r.status_code == 204, r.text
            assert await rows(sid) == ["keep me", "secret two"]
            assert await touched(sid) > before
            assert (
                await c.delete(f"/chat/sessions/{sid}/messages/{ids[0]}", headers=h)
            ).status_code == 404

            r = await c.delete(f"/chat/admin/sessions/{sid}/messages/{ids[2]}",
                               headers={"X-Test-Who": "admin"})
            assert r.status_code == 204, r.text
            assert await rows(sid) == ["keep me"]
    finally:
        await engine.dispose()
