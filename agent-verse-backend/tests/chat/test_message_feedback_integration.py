"""CHAT-D-2 on a real Postgres, as a least-privilege NOBYPASSRLS role.

* Feedback on a reply is durable and shared by replicas: one replica saves it,
  another lists it with the message; rating again edits the same row (one row
  per person per reply); it can be cleared.
* Owner-only: another person gets 404 and changes nothing; the restrictive RLS
  policy alone hides another principal's feedback.
* A reply that carries a goal feeds ``goal_feedback`` (the self-improvement
  signal) in the same transaction, re-queued on edit and removed on clear; a
  reply without a goal of this tenant adds nothing there.
* Deleting the message removes its feedback (ON DELETE CASCADE under RLS).

    DOCKER_HOST=unix:///Users/harsh/.colima/default/docker.sock \\
    TESTCONTAINERS_RYUK_DISABLED=true \\
        uv run pytest tests/chat/test_message_feedback_integration.py -m integration
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.chat.ownership import ChatScope
from app.chat.repository import PostgresChatRepository, goal_feedback_id
from app.chat.router import router as chat_router
from app.chat.service import ChatService
from app.tenancy.context import PlanTier, TenantContext
from tests.rag._pg import admin_exec, app_engine, seed_tenant, sessions

pytestmark = pytest.mark.integration

_OPS = ("operator",)


def _app(engine: Any, t: str) -> tuple[Any, ChatService]:
    from fastapi import FastAPI

    callers = {
        "a": TenantContext(t, PlanTier.FREE, "user:user-a", roles=_OPS, user_id="user-a"),
        "b": TenantContext(t, PlanTier.FREE, "user:user-b", roles=_OPS, user_id="user-b"),
    }
    app = FastAPI()
    svc = ChatService(repository=PostgresChatRepository(sessions(engine)))
    app.state.chat_service = svc

    @app.middleware("http")
    async def _caller(request: Any, call_next: Any) -> Any:
        request.state.tenant = callers[request.headers["X-Test-Who"]]
        return await call_next(request)

    app.include_router(chat_router)
    return app, svc


def _h(who: str) -> dict[str, str]:
    return {"X-Test-Who": who}


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://x")


async def _feedback_rows(pg_url: str, t: str) -> list[tuple[Any, ...]]:
    return list(
        await admin_exec(
            pg_url,
            "SELECT message_id, owner_principal, rating, comment FROM chat_message_feedback "
            "WHERE tenant_id = :t ORDER BY message_id",
            {"t": t},
        )
    )


async def test_feedback_is_durable_idempotent_editable_and_owner_only(pg_url: str) -> None:
    t = "t-" + uuid.uuid4().hex[:10]
    await seed_tenant(pg_url, t)
    gid = "g" + uuid.uuid4().hex[:20]
    await admin_exec(
        pg_url,
        "INSERT INTO goals (id, tenant_id, goal_text, status, dry_run) "
        "VALUES (:id, :t, 'deploy', 'complete', false)",
        {"id": gid, "t": t},
    )
    engine = await app_engine(pg_url)
    try:
        (app1, svc1), (app2, _svc2) = _app(engine, t), _app(engine, t)
        async with _client(app1) as c1, _client(app2) as c2:
            sid = (await c1.post("/chat/sessions", headers=_h("a"), json={})).json()["id"]
            user_mid = (
                await c1.post(f"/chat/sessions/{sid}/messages", headers=_h("a"),
                              json={"content": "what is the status?"})
            ).json()["message_id"]
            qa = await svc1.asave_message(session_id=sid, tenant_id=t, role="assistant",
                                          content="All green.")
            goal_reply = await svc1.adeliver_result(session_id=sid, tenant_id=t,
                                                    content="Deployed.", goal_id=gid)
            assert goal_reply is not None
            url = f"/chat/sessions/{sid}/messages/{{}}/feedback"

            up = await c1.post(url.format(qa.id), headers=_h("a"), json={"rating": 1})
            assert up.status_code == 200, up.text
            again = await c2.post(url.format(qa.id), headers=_h("a"), json={"rating": 1})
            assert again.status_code == 200
            edited = await c2.post(url.format(qa.id), headers=_h("a"),
                                   json={"rating": -1, "comment": "  not green at all  "})
            assert edited.json()["comment"] == "not green at all"
            assert await _feedback_rows(pg_url, t) == [
                (qa.id, "user:user-a", -1, "not green at all")
            ]

            # The other replica shows the saved state with the message.
            msgs = (await c1.get(f"/chat/sessions/{sid}/messages",
                                 headers=_h("a"))).json()["messages"]
            by_id = {m["id"]: m["feedback"] for m in msgs}
            assert by_id[qa.id]["rating"] == -1
            assert by_id[qa.id]["comment"] == "not green at all"
            assert by_id[user_mid] is None and by_id[goal_reply.id] is None

            # Owner-only; user messages are not rated.
            assert (await c2.post(url.format(qa.id), headers=_h("b"),
                                  json={"rating": 1})).status_code == 404
            assert (await c2.delete(url.format(qa.id), headers=_h("b"))).status_code == 404
            assert (await c2.post(url.format(user_mid), headers=_h("a"),
                                  json={"rating": 1})).status_code == 422
            assert len(await _feedback_rows(pg_url, t)) == 1

            # A goal reply feeds goal_feedback; an edit re-queues the same row.
            assert (await c1.post(url.format(goal_reply.id), headers=_h("a"),
                                  json={"rating": -1, "comment": "wrong env"})).status_code == 200
            gfid = goal_feedback_id(t, goal_reply.id, "user:user-a")
            await admin_exec(pg_url, "UPDATE goal_feedback SET processed_at = now() "
                             "WHERE id = :id", {"id": gfid})
            await c2.post(url.format(goal_reply.id), headers=_h("a"),
                          json={"rating": -1, "comment": "wrong env: staging"})
            gf = await admin_exec(
                pg_url,
                "SELECT id, goal_id, rating, correction, processed_at FROM goal_feedback "
                "WHERE tenant_id = :t",
                {"t": t},
            )
            assert [tuple(r) for r in gf] == [(gfid, gid, 1, "wrong env: staging", None)]

            # Clearing removes both.
            assert (await c1.delete(url.format(goal_reply.id),
                                    headers=_h("a"))).status_code == 204
            assert await admin_exec(
                pg_url, "SELECT id FROM goal_feedback WHERE tenant_id = :t", {"t": t}
            ) == []

            # Deleting the message removes its feedback.
            assert (await c1.delete(f"/chat/sessions/{sid}/messages/{qa.id}",
                                    headers=_h("a"))).status_code == 204
            assert await _feedback_rows(pg_url, t) == []
    finally:
        await engine.dispose()


async def test_a_reply_without_a_goal_of_this_tenant_adds_no_goal_feedback(
    pg_url: str,
) -> None:
    t = "t-" + uuid.uuid4().hex[:10]
    await seed_tenant(pg_url, t)
    engine = await app_engine(pg_url)
    try:
        repo = PostgresChatRepository(sessions(engine))
        svc = ChatService(repository=repo)
        s = await svc.acreate_session(t, owner_principal="user:user-a", owner_user_id="user-a")
        reply = await svc.asave_message(session_id=s.id, tenant_id=t, role="assistant",
                                        content="done", goal_id="not-a-goal")
        fb = await svc.asubmit_feedback(
            session_id=s.id, message_id=reply.id, tenant_id=t, rating=1, comment=None,
            scope=ChatScope.of("user:user-a"),
        )
        assert fb is not None and fb.rating == 1
        assert await admin_exec(
            pg_url, "SELECT id FROM goal_feedback WHERE tenant_id = :t", {"t": t}
        ) == []
    finally:
        await engine.dispose()


async def test_rls_alone_hides_another_principals_feedback(pg_url: str) -> None:
    t = "t-" + uuid.uuid4().hex[:10]
    await seed_tenant(pg_url, t)
    sid, mid = "s-" + t, "m-" + t
    await admin_exec(
        pg_url,
        "INSERT INTO chat_sessions (id, tenant_id, owner_principal) VALUES (:s, :t, 'user:user-a')",
        {"s": sid, "t": t},
    )
    await admin_exec(
        pg_url,
        "INSERT INTO chat_messages (id, session_id, tenant_id, role, content) "
        "VALUES (:m, :s, :t, 'assistant', 'hi')",
        {"m": mid, "s": sid, "t": t},
    )
    await admin_exec(
        pg_url,
        "INSERT INTO chat_message_feedback (id, tenant_id, session_id, message_id, "
        "owner_principal, rating) VALUES ('f-' || :t, :t, :s, :m, 'user:user-a', 1)",
        {"s": sid, "t": t, "m": mid},
    )
    engine = await app_engine(pg_url)
    try:
        async def run(principal: str, sql: str) -> Any:
            async with sessions(engine)() as s, s.begin():
                await s.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": t})
                await s.execute(
                    text("SELECT set_config('app.chat_principal', :p, true)"), {"p": principal}
                )
                result = await s.execute(text(sql), {"t": t, "m": mid, "s": sid})
                return result.fetchall() if result.returns_rows else result.rowcount

        sql = "SELECT id FROM chat_message_feedback WHERE tenant_id = :t"
        assert len(await run("user:user-a", sql)) == 1
        assert await run("user:user-b", sql) == []
        assert await run(
            "user:user-b", "UPDATE chat_message_feedback SET rating = -1 WHERE tenant_id = :t"
        ) == 0
        with pytest.raises(Exception, match="row-level security"):
            await run(
                "user:user-b",
                "INSERT INTO chat_message_feedback (id, tenant_id, session_id, message_id, "
                "owner_principal, rating) VALUES ('x-' || :t, :t, :s, :m, 'user:user-a', -1)",
            )
    finally:
        await engine.dispose()
