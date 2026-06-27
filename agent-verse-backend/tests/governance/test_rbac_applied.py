"""Tests that RBAC is actually applied to protected endpoints."""
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import create_app


@pytest.mark.asyncio
async def test_emergency_stop_requires_admin():
    """POST /governance/emergency-stop should check admin role."""
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # Without auth → 401
        r = await c.post("/governance/emergency-stop")
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_set_budget_requires_admin():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.put("/governance/budget", json={"per_goal_usd": 10.0, "per_tenant_daily_usd": 500.0})
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_approve_requires_approver_role():
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post("/governance/approvals/test-id/approve", json={"approver": "me", "note": ""})
        assert r.status_code == 401
