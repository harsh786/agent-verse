"""CHAT-SEC-1 on a real Postgres, as a least-privilege NOBYPASSRLS role.

* Through the real router and the Postgres repository: user B, an API key and a
  person of another tenant get 404 on A's session; lists and searches hold only
  the caller's own sessions; an unowned session is reachable only through the
  audited admin routes, and an admin can assign it to a person.
* The restrictive RLS policies alone (a query with NO owner predicate) narrow
  ``chat_sessions``, ``chat_messages`` and ``chat_artifacts`` to the principal
  set in ``app.chat_principal``, and refuse writes into another's session.
* The migration backfills ``owner_principal`` from ``owner_user_id``.

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/chat/test_session_ownership_integration.py -m integration
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
from app.governance.audit import AuditLog
from app.tenancy.context import PlanTier, TenantContext
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = pytest.mark.integration

_OPS = ("operator",)


def _callers(t: str) -> dict[str, TenantContext]:
    return {
        "a": TenantContext(t, PlanTier.FREE, "user:user-a", roles=_OPS, user_id="user-a"),
        "b": TenantContext(t, PlanTier.FREE, "user:user-b", roles=_OPS, user_id="user-b"),
        "admin": TenantContext(t, PlanTier.FREE, "user:adm", roles=("admin",), user_id="adm"),
        "key1": TenantContext(t, PlanTier.FREE, "key-1", roles=_OPS),
        "key2": TenantContext(t, PlanTier.FREE, "key-2", roles=_OPS),
        "other": TenantContext("other-" + t, PlanTier.FREE, "user:user-a", roles=_OPS,
                               user_id="user-a"),
    }


def _app(engine: Any, t: str) -> Any:
    from fastapi import FastAPI

    callers = _callers(t)
    app = FastAPI()
    app.state.chat_service = ChatService(repository=PostgresChatRepository(sessions(engine)))
    app.state.audit_log = AuditLog()

    @app.middleware("http")
    async def _caller(request: Any, call_next: Any) -> Any:
        request.state.tenant = callers[request.headers["X-Test-Who"]]
        return await call_next(request)

    app.include_router(chat_router)
    return app


def _h(who: str) -> dict[str, str]:
    return {"X-Test-Who": who}


async def test_owner_only_access_through_the_api_on_the_app_role(pg_url: str) -> None:
    t = "t-" + uuid.uuid4().hex[:10]
    await seed_tenant(pg_url, t)
    engine = await app_engine(pg_url)
    try:
        app = _app(engine, t)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://x") as c:
            sid = (await c.post("/chat/sessions", headers=_h("a"), json={"title": "A"})).json()[
                "id"
            ]
            sent = await c.post(
                f"/chat/sessions/{sid}/messages", headers=_h("a"),
                json={"content": "the launch code is tangerine"},
            )
            assert sent.status_code == 200, sent.text
            mid = sent.json()["message_id"]
            k_sid = (await c.post("/chat/sessions", headers=_h("key1"), json={})).json()["id"]

            for who in ("b", "key1", "admin", "other"):
                for method, url, kw in (
                    ("GET", f"/chat/sessions/{sid}", {}),
                    ("GET", f"/chat/sessions/{sid}/messages", {}),
                    ("GET", f"/chat/sessions/{sid}/export", {}),
                    ("GET", f"/chat/sessions/{sid}/search?q=tangerine", {}),
                    ("POST", f"/chat/sessions/{sid}/summarize", {}),
                    ("GET", f"/chat/sessions/{sid}/stream?message_id={mid}", {}),
                    ("PATCH", f"/chat/sessions/{sid}", {"json": {"title": "pwned"}}),
                    ("POST", f"/chat/sessions/{sid}/messages", {"json": {"content": "x"}}),
                    ("PATCH", f"/chat/sessions/{sid}/messages/{mid}", {"json": {"content": "y"}}),
                    ("DELETE", f"/chat/sessions/{sid}/messages/{mid}", {}),
                    ("DELETE", f"/chat/sessions/{sid}", {}),
                ):
                    r = await c.request(method, url, headers=_h(who), **kw)
                    assert r.status_code == 404, (who, method, url, r.status_code, r.text)
                listed = (await c.get("/chat/sessions", headers=_h(who))).json()["sessions"]
                assert sid not in {s["id"] for s in listed}
                hits = (
                    await c.post("/chat/search", headers=_h(who), json={"query": "tangerine"})
                ).json()
                assert hits["results"] == []

            # A still has everything, untouched.
            own = (await c.get(f"/chat/sessions/{sid}", headers=_h("a"))).json()
            assert own["title"] == "A" and own["owner_principal"] == "user:user-a"
            msgs = (await c.get(f"/chat/sessions/{sid}/messages", headers=_h("a"))).json()
            assert [m["content"] for m in msgs["messages"]] == ["the launch code is tangerine"]
            assert (
                await c.post("/chat/search", headers=_h("a"), json={"query": "tangerine"})
            ).json()["total"] == 1
            assert [
                s["id"] for s in (await c.get("/chat/sessions", headers=_h("a"))).json()["sessions"]
            ] == [sid]
            # The API-key rule: key1 sees only its own; key2 and a person cannot reach it.
            assert [
                s["id"]
                for s in (await c.get("/chat/sessions", headers=_h("key1"))).json()["sessions"]
            ] == [k_sid]
            assert (await c.get(f"/chat/sessions/{k_sid}", headers=_h("key2"))).status_code == 404
            assert (await c.get(f"/chat/sessions/{k_sid}", headers=_h("a"))).status_code == 404

            # An unowned (legacy / channel) session: admin routes only, audited.
            legacy = uuid.uuid4().hex
            await admin_exec(
                pg_url,
                "INSERT INTO chat_sessions (id, tenant_id, title) VALUES (:id, :t, 'legacy')",
                {"id": legacy, "t": t},
            )
            await admin_exec(
                pg_url,
                "INSERT INTO chat_messages (id, session_id, tenant_id, role, content) "
                "VALUES (:id, :s, :t, 'user', 'old words')",
                {"id": uuid.uuid4().hex, "s": legacy, "t": t},
            )
            for who in ("a", "b", "key1", "admin"):
                assert (
                    await c.get(f"/chat/sessions/{legacy}", headers=_h(who))
                ).status_code == 404
            unowned = (await c.get("/chat/admin/sessions/unowned", headers=_h("admin"))).json()
            assert [s["id"] for s in unowned["sessions"]] == [legacy]
            read = await c.get(f"/chat/admin/sessions/{legacy}/messages", headers=_h("admin"))
            assert [m["content"] for m in read.json()["messages"]] == ["old words"]
            # The admin routes never reach an owned session.
            assert (
                await c.get(f"/chat/admin/sessions/{sid}/messages", headers=_h("admin"))
            ).status_code == 404
            assigned = await c.post(
                f"/chat/admin/sessions/{legacy}/assign", headers=_h("admin"),
                json={"owner_user_id": "user-b"},
            )
            assert assigned.status_code == 200, assigned.text
            assert (await c.get(f"/chat/sessions/{legacy}", headers=_h("b"))).status_code == 200
            assert (await c.get(f"/chat/sessions/{legacy}", headers=_h("a"))).status_code == 404
            audited = app.state.audit_log.query(tenant_ctx=_callers(t)["admin"], goal_id="chat.admin")
            assert {e.tool_name for e in audited} == {
                "chat.admin.list_unowned", "chat.admin.read_unowned", "chat.admin.assign",
            }

            # A deletes its own session: gone, with its messages.
            assert (await c.delete(f"/chat/sessions/{sid}", headers=_h("a"))).status_code == 204
        left = await admin_exec(
            pg_url, "SELECT count(*) FROM chat_messages WHERE session_id = :s", {"s": sid}
        )
        assert left[0][0] == 0
    finally:
        await engine.dispose()


async def test_rls_alone_narrows_to_the_principal(pg_url: str) -> None:
    """No owner predicate in the SQL: the restrictive policies still hold."""
    t = "t-" + uuid.uuid4().hex[:10]
    await seed_tenant(pg_url, t)
    ids = {who: uuid.uuid4().hex for who in ("a", "b", "none")}
    for who, sid in ids.items():
        await admin_exec(
            pg_url,
            "INSERT INTO chat_sessions (id, tenant_id, title, owner_principal) "
            "VALUES (:id, :t, :w, :p)",
            {"id": sid, "t": t, "w": who, "p": None if who == "none" else f"user:user-{who}"},
        )
        await admin_exec(
            pg_url,
            "INSERT INTO chat_messages (id, session_id, tenant_id, role, content) "
            "VALUES (:id, :s, :t, 'user', :c)",
            {"id": uuid.uuid4().hex, "s": sid, "t": t, "c": f"msg-{who}"},
        )
        await admin_exec(
            pg_url,
            "INSERT INTO chat_artifacts (id, tenant_id, kind, session_id, title, mime, content, "
            "size_bytes) VALUES (:id, :t, 'snippet', :s, :w, 'text/plain', '\\x00', 1)",
            {"id": uuid.uuid4().hex, "t": t, "s": sid, "w": who},
        )
    engine = await app_engine(pg_url)

    async def run(principal: str | None, sql: str, params: dict[str, Any] | None = None) -> Any:
        async with engine.connect() as conn, conn.begin():
            await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": t})
            if principal is not None:
                await conn.execute(
                    text("SELECT set_config('app.chat_principal', :p, true)"), {"p": principal}
                )
            result = await conn.execute(text(sql), {"t": t, **(params or {})})
            return result.fetchall() if result.returns_rows else result.rowcount

    try:
        def titles(rows: Any) -> set[str]:
            return {str(r[0]) for r in rows}

        sessions_sql = "SELECT title FROM chat_sessions WHERE tenant_id = :t"
        msgs_sql = "SELECT content FROM chat_messages WHERE tenant_id = :t"
        arts_sql = "SELECT title FROM chat_artifacts WHERE tenant_id = :t"
        assert titles(await run("user:user-b", sessions_sql)) == {"b"}
        assert titles(await run("user:user-b", msgs_sql)) == {"msg-b"}
        assert titles(await run("user:user-b", arts_sql)) == {"b"}
        assert titles(await run("unowned", sessions_sql)) == {"none"}
        assert titles(await run("unowned", msgs_sql)) == {"msg-none"}
        assert titles(await run("key:nobody", sessions_sql)) == set()
        # Not narrowed when no principal is set (an internal, authorized path).
        assert titles(await run(None, sessions_sql)) == {"a", "b", "none"}
        # Writes into another principal's session match nothing / are refused.
        assert await run(
            "user:user-b", "UPDATE chat_sessions SET title = 'pwned' WHERE id = :s",
            {"s": ids["a"]},
        ) == 0
        assert await run(
            "user:user-b", "DELETE FROM chat_messages WHERE session_id = :s", {"s": ids["a"]}
        ) == 0
        with pytest.raises(Exception, match="row-level security"):
            await run(
                "user:user-b",
                "INSERT INTO chat_messages (id, session_id, tenant_id, role, content) "
                "VALUES (:id, :s, :t, 'user', 'injected')",
                {"id": uuid.uuid4().hex, "s": ids["a"]},
            )
        with pytest.raises(Exception, match="row-level security"):
            await run(
                "user:user-b",
                "UPDATE chat_sessions SET owner_principal = 'user:user-a' WHERE id = :s",
                {"s": ids["b"]},
            )
        assert titles(await run("user:user-a", sessions_sql)) == {"a"}
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


async def test_migration_backfills_the_owner_from_owner_user_id(pg_url: str) -> None:
    t = "t-" + uuid.uuid4().hex[:10]
    await seed_tenant(pg_url, t)
    _alembic(pg_url, "downgrade", "c3e8a1f5b7d2")
    try:
        owned, unowned = uuid.uuid4().hex, uuid.uuid4().hex
        await admin_exec(
            pg_url,
            "INSERT INTO chat_sessions (id, tenant_id, owner_user_id) VALUES "
            "(:a, :t, 'user-a'), (:b, :t, NULL)",
            {"a": owned, "b": unowned, "t": t},
        )
    finally:
        _alembic(pg_url, "upgrade", "head")
    rows = dict(
        await admin_exec(
            pg_url,
            "SELECT id, owner_principal FROM chat_sessions WHERE tenant_id = :t",
            {"t": t},
        )
    )
    assert rows == {owned: "user:user-a", unowned: None}
