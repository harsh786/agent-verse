"""Integration tests for chat API endpoints — 25 cases."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture()
def app():
    from app.main import create_app
    return create_app()


@pytest.fixture()
async def client_with_tenant(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "ChatTest", "email": "chat@test.com"})
        assert r.status_code in (200, 201), r.text
        api_key = r.json()["api_key"]
        c.headers["X-API-Key"] = api_key
        yield c


# ── Auth ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_requires_auth(app) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/chat/sessions", json={"title": "test"})
        assert r.status_code == 401


# ── Session CRUD ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_session(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "My Chat"})
    assert r.status_code == 201
    data = r.json()
    assert data["title"] == "My Chat"
    assert data["id"]


@pytest.mark.asyncio
async def test_list_sessions(client_with_tenant) -> None:
    await client_with_tenant.post("/chat/sessions", json={"title": "S1"})
    await client_with_tenant.post("/chat/sessions", json={"title": "S2"})
    r = await client_with_tenant.get("/chat/sessions")
    assert r.status_code == 200
    assert len(r.json()["sessions"]) >= 2


@pytest.mark.asyncio
async def test_get_session(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "Test"})
    sid = r.json()["id"]
    r2 = await client_with_tenant.get(f"/chat/sessions/{sid}")
    assert r2.status_code == 200
    assert r2.json()["id"] == sid


@pytest.mark.asyncio
async def test_get_session_not_found(client_with_tenant) -> None:
    r = await client_with_tenant.get("/chat/sessions/nonexistent123")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_session(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "Old"})
    sid = r.json()["id"]
    r2 = await client_with_tenant.patch(f"/chat/sessions/{sid}", json={"title": "New"})
    assert r2.status_code == 200
    assert r2.json()["title"] == "New"


@pytest.mark.asyncio
async def test_delete_session(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "Del"})
    sid = r.json()["id"]
    r2 = await client_with_tenant.delete(f"/chat/sessions/{sid}")
    assert r2.status_code == 204
    r3 = await client_with_tenant.get(f"/chat/sessions/{sid}")
    assert r3.status_code == 404


@pytest.mark.asyncio
async def test_pin_session(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "Pin me"})
    sid = r.json()["id"]
    r2 = await client_with_tenant.post(f"/chat/sessions/{sid}/pin?pinned=true")
    assert r2.status_code == 200
    assert r2.json()["pinned"] is True


# ── Message endpoints ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_qa_message(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "QA"})
    sid = r.json()["id"]
    r2 = await client_with_tenant.post(
        f"/chat/sessions/{sid}/messages",
        json={"content": "What is Python?"},
    )
    assert r2.status_code == 200
    data = r2.json()
    assert data["intent"] == "QA"
    assert data["message_id"]


@pytest.mark.asyncio
async def test_send_goal_message(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "Goal"})
    sid = r.json()["id"]
    r2 = await client_with_tenant.post(
        f"/chat/sessions/{sid}/messages",
        json={"content": "Deploy the backend service to production"},
    )
    assert r2.status_code == 200
    assert r2.json()["intent"] == "GOAL"


@pytest.mark.asyncio
async def test_send_schedule_message(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "Sched"})
    sid = r.json()["id"]
    r2 = await client_with_tenant.post(
        f"/chat/sessions/{sid}/messages",
        json={"content": "Run the backup every day at 2 AM"},
    )
    assert r2.status_code == 200
    data = r2.json()
    assert data["intent"] == "SCHEDULE"
    assert data["schedule_confirmation"] is not None


@pytest.mark.asyncio
async def test_list_messages(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "Msgs"})
    sid = r.json()["id"]
    await client_with_tenant.post(f"/chat/sessions/{sid}/messages", json={"content": "Hello"})
    r2 = await client_with_tenant.get(f"/chat/sessions/{sid}/messages")
    assert r2.status_code == 200
    assert len(r2.json()["messages"]) >= 1


@pytest.mark.asyncio
async def test_edit_message(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "Edit"})
    sid = r.json()["id"]
    r2 = await client_with_tenant.post(
        f"/chat/sessions/{sid}/messages", json={"content": "What is Python?"}
    )
    mid = r2.json()["message_id"]
    r3 = await client_with_tenant.patch(
        f"/chat/sessions/{sid}/messages/{mid}", json={"content": "What is Rust?"}
    )
    assert r3.status_code == 200


# ── SSE streaming ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stream_qa_response(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "Stream"})
    sid = r.json()["id"]
    r2 = await client_with_tenant.post(
        f"/chat/sessions/{sid}/messages", json={"content": "What is Docker?"}
    )
    mid = r2.json()["message_id"]
    r3 = await client_with_tenant.get(
        f"/chat/sessions/{sid}/stream?message_id={mid}",
        headers={"Accept": "text/event-stream"},
    )
    assert r3.status_code == 200
    assert "text/event-stream" in r3.headers.get("content-type", "")


# ── Usage ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_usage(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "Usage"})
    sid = r.json()["id"]
    r2 = await client_with_tenant.get(f"/chat/sessions/{sid}/usage")
    assert r2.status_code == 200
    data = r2.json()
    assert "total_tokens" in data


# ── Summary ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_summarize_session(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "Sum"})
    sid = r.json()["id"]
    await client_with_tenant.post(f"/chat/sessions/{sid}/messages", json={"content": "Deploy Kubernetes"})
    r2 = await client_with_tenant.post(f"/chat/sessions/{sid}/summarize")
    assert r2.status_code == 200
    assert "summary" in r2.json()


# ── Search ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_search_messages(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "Search"})
    sid = r.json()["id"]
    await client_with_tenant.post(f"/chat/sessions/{sid}/messages", json={"content": "What is FastAPI?"})
    r2 = await client_with_tenant.post("/chat/search", json={"query": "FastAPI"})
    assert r2.status_code == 200
    assert r2.json()["total"] >= 1


# ── Folders ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_list_folder(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/folders", json={"name": "Work", "color": "#ff0000"})
    assert r.status_code == 201
    r2 = await client_with_tenant.get("/chat/folders")
    assert len(r2.json()["folders"]) >= 1


@pytest.mark.asyncio
async def test_delete_folder(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/folders", json={"name": "Del"})
    fid = r.json()["id"]
    r2 = await client_with_tenant.delete(f"/chat/folders/{fid}")
    assert r2.status_code == 204


# ── Artifacts ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_list_artifact(client_with_tenant) -> None:
    r = await client_with_tenant.post("/chat/sessions", json={"title": "Art"})
    sid = r.json()["id"]
    r2 = await client_with_tenant.post(
        f"/chat/sessions/{sid}/artifacts",
        json={"title": "hello.py", "language": "python", "content": "print('hi')"},
    )
    assert r2.status_code == 201
    r3 = await client_with_tenant.get(f"/chat/sessions/{sid}/artifacts")
    assert len(r3.json()["artifacts"]) == 1


# ── Models ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_models(client_with_tenant) -> None:
    r = await client_with_tenant.get("/chat/models")
    assert r.status_code == 200
    models = r.json()["models"]
    assert len(models) >= 4
