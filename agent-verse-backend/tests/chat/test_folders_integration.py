"""CHAT-D-1 on a real Postgres, as a least-privilege NOBYPASSRLS role.

* Folders are durable and shared by replicas: one ChatService creates, renames,
  files a session and deletes; a second ChatService over the same database (an
  other replica, or the same one after a restart) sees each change.
* Owner-only through the router: another person, an API key and another tenant
  see no folder and get 404 on rename/delete/move; a session can be filed only
  into a folder of its own owner (move, PATCH and create).
* Deleting a folder unfiles its sessions (ON DELETE SET NULL under RLS).
* The migration unfiles sessions filed into a folder that was never persisted.
* The restrictive RLS policy alone (no owner predicate) narrows
  ``chat_session_folders`` to the principal in ``app.chat_principal``.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/chat/test_folders_integration.py -m integration
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.chat.repository import PostgresChatRepository
from app.chat.router import router as chat_router
from app.chat.service import ChatService
from app.tenancy.context import PlanTier, TenantContext
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = pytest.mark.integration

_OPS = ("operator",)


def _callers(t: str) -> dict[str, TenantContext]:
    return {
        "a": TenantContext(t, PlanTier.FREE, "user:user-a", roles=_OPS, user_id="user-a"),
        "b": TenantContext(t, PlanTier.FREE, "user:user-b", roles=_OPS, user_id="user-b"),
        "key1": TenantContext(t, PlanTier.FREE, "key-1", roles=_OPS),
        "other": TenantContext("other-" + t, PlanTier.FREE, "user:user-a", roles=_OPS,
                               user_id="user-a"),
    }


def _app(engine: Any, t: str) -> Any:
    from fastapi import FastAPI

    callers = _callers(t)
    app = FastAPI()
    app.state.chat_service = ChatService(repository=PostgresChatRepository(sessions(engine)))

    @app.middleware("http")
    async def _caller(request: Any, call_next: Any) -> Any:
        request.state.tenant = callers[request.headers["X-Test-Who"]]
        return await call_next(request)

    app.include_router(chat_router)
    return app


def _h(who: str) -> dict[str, str]:
    return {"X-Test-Who": who}


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://x")


async def test_folders_are_durable_across_replicas_and_owner_only(pg_url: str) -> None:
    t = "t-" + uuid.uuid4().hex[:10]
    await seed_tenant(pg_url, t)
    await seed_tenant(pg_url, "other-" + t)
    engine = await app_engine(pg_url)
    try:
        r1, r2 = _app(engine, t), _app(engine, t)  # two replicas, one database
        async with _client(r1) as c1, _client(r2) as c2:
            made = await c1.post("/chat/folders", headers=_h("a"),
                                 json={"name": "Work", "color": "#112233"})
            assert made.status_code == 201, made.text
            fid = made.json()["id"]
            second = await c1.post("/chat/folders", headers=_h("a"), json={"name": "Later"})
            assert second.json()["position"] == made.json()["position"] + 1

            listed = (await c2.get("/chat/folders", headers=_h("a"))).json()["folders"]
            assert [f["id"] for f in listed] == [fid, second.json()["id"]]
            assert listed[0]["color"] == "#112233"

            for who in ("b", "key1", "other"):
                assert (await c2.get("/chat/folders", headers=_h(who))).json()["folders"] == []
                assert (await c2.patch(f"/chat/folders/{fid}", headers=_h(who),
                                       json={"name": "pwned"})).status_code == 404
                assert (await c2.delete(f"/chat/folders/{fid}",
                                        headers=_h(who))).status_code == 404

            renamed = await c2.patch(f"/chat/folders/{fid}", headers=_h("a"),
                                     json={"name": "Clients"})
            assert renamed.status_code == 200, renamed.text
            assert renamed.json()["name"] == "Clients"
            assert renamed.json()["color"] == "#112233"  # untouched
            after = (await c1.get("/chat/folders", headers=_h("a"))).json()["folders"]
            assert after[0]["name"] == "Clients"

            # Filing: own folder only, on every path.
            sid = (await c1.post("/chat/sessions", headers=_h("a"), json={})).json()["id"]
            sb = (await c1.post("/chat/sessions", headers=_h("b"), json={})).json()["id"]
            for resp in (
                await c1.post(f"/chat/sessions/{sb}/move", headers=_h("b"),
                              params={"folder_id": fid}),
                await c1.patch(f"/chat/sessions/{sb}", headers=_h("b"), json={"folder_id": fid}),
                await c1.post("/chat/sessions", headers=_h("b"), json={"folder_id": fid}),
            ):
                assert resp.status_code == 404
                assert resp.json()["detail"] == "Folder not found"
            assert (await c1.post(f"/chat/sessions/{sid}/move", headers=_h("b"),
                                  params={"folder_id": fid})).status_code == 404

            moved = await c1.post(f"/chat/sessions/{sid}/move", headers=_h("a"),
                                  params={"folder_id": fid})
            assert moved.status_code == 200, moved.text
            got = (await c2.get(f"/chat/sessions/{sid}", headers=_h("a"))).json()
            assert got["folder_id"] == fid
            filed = await c2.post("/chat/sessions", headers=_h("a"), json={"folder_id": fid})
            assert filed.status_code == 201 and filed.json()["folder_id"] == fid

            # Deleting the folder (on the other replica) unfiles its sessions.
            assert (await c2.delete(f"/chat/folders/{fid}", headers=_h("a"))).status_code == 204
            for s in (sid, filed.json()["id"]):
                got = (await c1.get(f"/chat/sessions/{s}", headers=_h("a"))).json()
                assert got["folder_id"] is None
            left = (await c1.get("/chat/folders", headers=_h("a"))).json()["folders"]
            assert [f["id"] for f in left] == [second.json()["id"]]

            unfiled = await c1.post(f"/chat/sessions/{sid}/move", headers=_h("a"))
            assert unfiled.status_code == 200 and unfiled.json()["folder_id"] is None

        rows = await admin_exec(
            pg_url, "SELECT owner_principal FROM chat_session_folders WHERE tenant_id = :t",
            {"t": t},
        )
        assert [r[0] for r in rows] == ["user:user-a"]
    finally:
        await engine.dispose()


async def test_rls_alone_narrows_folders_to_the_principal(pg_url: str) -> None:
    t = "t-" + uuid.uuid4().hex[:10]
    await seed_tenant(pg_url, t)
    await admin_exec(
        pg_url,
        "INSERT INTO chat_session_folders (id, tenant_id, name, owner_principal) VALUES "
        "('fa-' || :t, :t, 'A', 'user:user-a'), ('fb-' || :t, :t, 'B', 'user:user-b')",
        {"t": t},
    )
    engine = await app_engine(pg_url)
    try:
        async def run(principal: str, sql: str) -> Any:
            async with sessions(engine)() as s, s.begin():
                await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": t})
                await s.execute(
                    text("SELECT set_config('app.chat_principal', :p, true)"), {"p": principal}
                )
                result = await s.execute(text(sql), {"t": t})
                return result.fetchall() if result.returns_rows else result.rowcount

        seen = await run("user:user-b", "SELECT id FROM chat_session_folders WHERE tenant_id = :t")
        assert [r[0] for r in seen] == ["fb-" + t]
        assert await run(
            "user:user-b", "UPDATE chat_session_folders SET name = 'pwned' WHERE id = 'fa-' || :t"
        ) == 0
        assert await run(
            "user:user-b", "DELETE FROM chat_session_folders WHERE id = 'fa-' || :t"
        ) == 0
        with pytest.raises(Exception, match="row-level security"):
            await run(
                "user:user-b",
                "INSERT INTO chat_session_folders (id, tenant_id, name, owner_principal) "
                "VALUES ('fx-' || :t, :t, 'x', 'user:user-a')",
            )
        names = await admin_exec(
            pg_url, "SELECT name FROM chat_session_folders WHERE id = 'fa-' || :t", {"t": t}
        )
        assert [r[0] for r in names] == ["A"]
    finally:
        await engine.dispose()


def _alembic(pg_url: str, *args: str) -> None:
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "DATABASE_URL": pg_url, "ENVIRONMENT": "development"}
    done = subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=root, env=env,
        capture_output=True, text=True, check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]


async def test_migration_unfiles_sessions_of_folders_never_persisted(pg_url: str) -> None:
    t = "t-" + uuid.uuid4().hex[:10]
    await seed_tenant(pg_url, t)
    _alembic(pg_url, "downgrade", "e9a3c5d7f1b2")
    try:
        await admin_exec(
            pg_url,
            "INSERT INTO chat_session_folders (id, tenant_id, name) VALUES ('kept-' || :t, :t, 'k')",
            {"t": t},
        )
        await admin_exec(
            pg_url,
            "INSERT INTO chat_sessions (id, tenant_id, folder_id) VALUES "
            "('lost-' || :t, :t, 'memory-only-folder'), ('kept-' || :t, :t, 'kept-' || :t), "
            "('none-' || :t, :t, NULL)",
            {"t": t},
        )
    finally:
        _alembic(pg_url, "upgrade", "head")
    rows = dict(
        await admin_exec(
            pg_url, "SELECT id, folder_id FROM chat_sessions WHERE tenant_id = :t", {"t": t}
        )
    )
    assert rows == {"lost-" + t: None, "kept-" + t: "kept-" + t, "none-" + t: None}
