"""Phase 4 — POST /chat/sessions/{id}/attachments end-to-end."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.mark.asyncio
async def test_upload_attachment_adds_context_message() -> None:
    app = create_app(manage_pools=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "Up", "email": "up@example.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        sid = (await c.post("/chat/sessions", json={"title": "files"})).json()["id"]

        up = await c.post(
            f"/chat/sessions/{sid}/attachments",
            files={"file": ("notes.txt", b"the launch date is March 3", "text/plain")},
        )
        assert up.status_code == 201
        assert "March 3" in up.json()["content"]

        msgs = (await c.get(f"/chat/sessions/{sid}/messages")).json()["messages"]
        assert any("March 3" in m["content"] for m in msgs)


@pytest.mark.asyncio
async def test_upload_to_missing_session_404() -> None:
    app = create_app(manage_pools=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "Up", "email": "up2@example.com"})
        c.headers["X-API-Key"] = r.json()["api_key"]
        up = await c.post(
            "/chat/sessions/nope/attachments",
            files={"file": ("a.txt", b"x", "text/plain")},
        )
        assert up.status_code == 404
