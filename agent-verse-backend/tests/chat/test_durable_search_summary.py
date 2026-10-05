"""ORG-32 / a08-F186-01: chat search and session summary read the durable store.

POST /chat/search, POST /chat/sessions/{id}/summarize and GET
/chat/sessions/{id}/search read ChatService's in-memory dict (empty when the
Postgres repository is attached) or substring-scanned a bounded window of
recent messages, so they returned nothing or missed older messages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.chat.service import ChatService


def _row(mid: str, sid: str, role: str, content: str) -> dict[str, Any]:
    return {
        "id": mid,
        "session_id": sid,
        "tenant_id": "t1",
        "role": role,
        "content": content,
        "created_at": datetime.now(UTC),
    }


class _Repo:
    def __init__(self) -> None:
        self.rows = [
            _row("m1", "s1", "user", "How do I rotate the vault encryption keys?"),
            _row("m2", "s1", "assistant", "Use the key rotation endpoint."),
        ]
        self.search_calls: list[tuple[str, str, str | None, int]] = []
        self.total = 2

    async def get_session(self, session_id: str, tenant_id: str) -> dict[str, Any] | None:
        if session_id == "s1" and tenant_id == "t1":
            return {"id": "s1", "tenant_id": "t1", "title": "keys"}
        return None

    async def count_messages(self, session_id: str, tenant_id: str) -> int:
        return self.total if (session_id, tenant_id) == ("s1", "t1") else 0

    async def search_messages(
        self, tenant_id: str, query: str, *, session_id: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        self.search_calls.append((tenant_id, query, session_id, limit))
        return [r for r in self.rows if r["tenant_id"] == tenant_id and "rotat" in r["content"]]

    async def list_messages(self, session_id: str, tenant_id: str, **_: Any) -> list[dict]:
        return [r for r in self.rows if r["session_id"] == session_id]


async def test_db_mode_search_reads_the_repository() -> None:
    repo = _Repo()
    svc = ChatService(repository=repo)
    found = await svc.asearch_messages("t1", "rotate keys", session_id="s1", limit=5)
    assert [m.id for m in found] == ["m1", "m2"]
    assert repo.search_calls == [("t1", "rotate keys", "s1", 5)]
    assert await svc.asearch_messages("t2", "rotate") == []


async def test_db_mode_summary_reads_persisted_history() -> None:
    svc = ChatService(repository=_Repo())
    summary = await svc.asummarize_session("s1", "t1")
    assert "2 messages" in summary
    assert "rotate the vault" in summary


async def test_db_mode_summary_counts_the_whole_session_not_the_window() -> None:
    """The topics come from a bounded window; the message count must not."""
    repo = _Repo()
    repo.total = 12_345
    summary = await ChatService(repository=repo).asummarize_session("s1", "t1")
    assert "12345 messages" in summary, summary


async def test_in_memory_path_still_works() -> None:
    svc = ChatService()
    assert await svc.asummarize_session("missing", "t1") == "Empty session."
    assert await svc.asearch_messages("t1", "x") == []


def _route_app(repo: _Repo) -> Any:
    from fastapi import FastAPI, Request

    from app.chat.router import router
    from app.tenancy.context import PlanTier, TenantContext

    app = FastAPI()
    app.state.chat_service = ChatService(repository=repo)

    @app.middleware("http")
    async def _as_tenant(request: Request, call_next: Any) -> Any:
        request.state.tenant = TenantContext(
            tenant_id="t1", plan=PlanTier.PROFESSIONAL, api_key_id="k"
        )
        return await call_next(request)

    app.include_router(router)
    return app


async def _call(repo: _Repo, method: str, path: str, **kw: Any) -> Any:
    from httpx import ASGITransport, AsyncClient

    app = _route_app(repo)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.request(method, path, **kw)


async def test_within_session_search_route_uses_postgres_fts() -> None:
    repo = _Repo()
    r = await _call(repo, "GET", "/chat/sessions/s1/search", params={"q": "rotate", "limit": 7})
    assert r.status_code == 200, r.text
    body = r.json()
    assert repo.search_calls == [("t1", "rotate", "s1", 7)]
    assert [hit["message_id"] for hit in body["results"]] == ["m1", "m2"]
    assert body["results"][0]["session_id"] == "s1"
    assert "**rotate**" in body["results"][0]["snippet"]
    assert body["total"] == 2


async def test_within_session_search_of_an_unknown_session_is_404() -> None:
    repo = _Repo()
    r = await _call(repo, "GET", "/chat/sessions/nope/search", params={"q": "rotate"})
    assert r.status_code == 404
    assert repo.search_calls == []


async def test_search_and_summarize_routes_read_the_repository() -> None:
    repo = _Repo()
    r = await _call(repo, "POST", "/chat/search", json={"query": "rotate keys", "limit": 3})
    assert r.status_code == 200, r.text
    assert repo.search_calls == [("t1", "rotate keys", None, 3)]
    assert r.json()["total"] == 2

    r = await _call(repo, "POST", "/chat/sessions/s1/summarize")
    assert r.status_code == 200, r.text
    assert "2 messages" in r.json()["summary"]
