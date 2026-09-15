"""Phase 4 — GET /chat/artifacts/{id}/download end-to-end (auth + bytes)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.mark.asyncio
async def test_download_artifact_returns_bytes_with_disposition() -> None:
    app = create_app(manage_pools=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/tenants/signup", json={"name": "Art", "email": "art@example.com"})
        assert r.status_code == 201
        c.headers["X-API-Key"] = r.json()["api_key"]
        me = (await c.get("/tenants/me")).json()
        tenant_id = me.get("tenant_id") or me.get("id")
        assert tenant_id

        aid = app.state.chat_artifact_store.put(
            tenant_id=tenant_id, content=b"%PDF-1.4 body", mime="application/pdf", filename="r.pdf"
        )
        resp = await c.get(f"/chat/artifacts/{aid}/download")
        assert resp.status_code == 200
        assert resp.content == b"%PDF-1.4 body"
        assert resp.headers["content-type"].startswith("application/pdf")
        assert 'filename="r.pdf"' in resp.headers["content-disposition"]

        # Unknown artifact -> 404.
        missing = await c.get("/chat/artifacts/nope/download")
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_download_requires_auth() -> None:
    app = create_app(manage_pools=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/chat/artifacts/x/download")
        assert resp.status_code in (401, 403)
