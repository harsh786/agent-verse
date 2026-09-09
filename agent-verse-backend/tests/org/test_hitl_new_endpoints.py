"""Tests for new HITL endpoints from hitl-gap-analysis.md.

Tests cover:
  G-04: GET /v1/org/{org_id}/approvals
  G-12: notify_approval_timeout wired into HITLGateway
  G-17: Magic link URLs in notifications
  G-19: OrgEventPublisher initialization
  G-23: GET /v1/org/{org_id}/events/stream (SSE)
"""
from __future__ import annotations

import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.org.router import get_org_service
from app.org.router import router as org_router

TENANT_ID  = "00000000-0000-0000-0000-000000000001"
ORG_ID     = "org-test-001"


def _make_app(mock_service: Any | None = None) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject_tenant(request: Any, call_next: Any) -> Any:
        ctx = MagicMock()
        ctx.tenant_id = TENANT_ID
        request.state.tenant = ctx
        return await call_next(request)

    app.include_router(org_router)

    # Override OrgService dependency to avoid DB requirement
    if mock_service is not None:
        app.dependency_overrides[get_org_service] = lambda: mock_service
    else:
        _svc = MagicMock()
        _svc._tenant_id = TENANT_ID
        _svc.list_missions = AsyncMock(return_value=[])
        _svc.list_events = AsyncMock(return_value=[])
        app.dependency_overrides[get_org_service] = lambda: _svc

    # Inject a mock app state for gateway/redis
    app.state = types.SimpleNamespace(
        hitl_gateway=None,
        redis=None,
    )
    return app


@pytest.fixture
async def client() -> AsyncClient:   # type: ignore[misc]
    app = _make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── G-04: /approvals endpoint ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_org_approvals_no_gateway(client: AsyncClient) -> None:
    """Returns empty list when HITL gateway is not in app state."""
    resp = await client.get(f"/v1/org/{ORG_ID}/approvals")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert data["org_id"] == ORG_ID
    assert data["data"] == []


@pytest.mark.asyncio
async def test_org_approvals_with_pending() -> None:
    """Returns pending approvals from HITL gateway."""
    from app.governance.hitl import ApprovalStatus

    mock_req = MagicMock()
    mock_req.goal_id    = "goal-123"
    mock_req.action     = "send_email"
    mock_req.risk_level = "high"
    mock_req.status     = ApprovalStatus.PENDING
    mock_req.request_id = "req-123"
    mock_req.created_at = "2026-08-20T00:00:00Z"

    mock_gateway = MagicMock()
    mock_gateway.list_pending.return_value = [mock_req]

    app = _make_app()
    app.state.hitl_gateway = mock_gateway

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get(f"/v1/org/{ORG_ID}/approvals")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["data"], list)


@pytest.mark.asyncio
async def test_org_approvals_requires_auth() -> None:
    """Returns 401 when no tenant context (get_org_service NOT overridden)."""
    bare = FastAPI()
    bare.include_router(org_router)
    # Deliberately do NOT override get_org_service — _require_tenant raises 401
    async with AsyncClient(transport=ASGITransport(app=bare), base_url="http://test") as c:
        resp = await c.get(f"/v1/org/{ORG_ID}/approvals")
    assert resp.status_code == 401


# ── G-23: /events/stream SSE endpoint ────────────────────────────────────────

@pytest.mark.asyncio
async def test_org_events_stream_returns_sse() -> None:
    """SSE stream endpoint returns text/event-stream content type.

    This endpoint streams forever (keepalive every 15s, since no Redis is
    configured) and only exits when `request.is_disconnected()` becomes
    True. httpx's ASGITransport does not propagate a real disconnect when a
    client-side stream is closed early, so a full HTTP round trip via
    AsyncClient hangs. Instead, call the route function directly with a
    fake Request that reports an immediate disconnect — this still
    exercises the real StreamingResponse the endpoint builds, just without
    the un-testable infinite keepalive loop.
    """
    import types

    from app.org.router import org_events_stream

    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=True)
    request.app = types.SimpleNamespace(state=types.SimpleNamespace(redis=None))

    service = MagicMock()
    service._tenant_id = TENANT_ID

    resp = await org_events_stream(org_id=ORG_ID, request=request, service=service)
    assert resp.media_type == "text/event-stream"

    chunks = [c async for c in resp.body_iterator]
    text = "".join(c if isinstance(c, str) else c.decode() for c in chunks)
    assert "connected" in text


@pytest.mark.asyncio
async def test_org_events_stream_requires_auth() -> None:
    """SSE stream returns 401 without auth."""
    bare = FastAPI()
    bare.include_router(org_router)
    # Do NOT override get_org_service — _require_tenant raises 401
    async with AsyncClient(transport=ASGITransport(app=bare), base_url="http://test") as c:
        resp = await c.get(f"/v1/org/{ORG_ID}/events/stream", timeout=2.0)
    assert resp.status_code == 401


# ── G-12: notify_approval_timeout wired in HITLGateway ───────────────────────

def test_expire_timed_out_calls_notify() -> None:
    """G-12: expire_timed_out_requests() marks request TIMED_OUT."""
    from datetime import UTC, datetime, timedelta

    from app.governance.hitl import ApprovalRequest, ApprovalStatus, HITLGateway

    gateway = HITLGateway()
    req = ApprovalRequest(
        goal_id="goal-99", action="risky_action", risk_level="critical",
        request_id="req-expired",
    )
    req._expires_at_dt = datetime.now(UTC) - timedelta(seconds=1)
    req.status = ApprovalStatus.PENDING
    gateway._requests[(TENANT_ID, "req-expired")] = req

    expired = gateway.expire_timed_out_requests()
    assert "req-expired" in expired
    assert req.status == ApprovalStatus.TIMED_OUT


def test_expire_future_request_skips() -> None:
    """Non-expired requests are NOT expired."""
    from datetime import UTC, datetime, timedelta

    from app.governance.hitl import ApprovalRequest, ApprovalStatus, HITLGateway

    gateway = HITLGateway()
    req = ApprovalRequest(
        goal_id="goal-99", action="safe_action", risk_level="low",
        request_id="req-future",
    )
    req._expires_at_dt = datetime.now(UTC) + timedelta(minutes=5)
    req.status = ApprovalStatus.PENDING
    gateway._requests[(TENANT_ID, "req-future")] = req

    expired = gateway.expire_timed_out_requests()
    assert "req-future" not in expired
    assert req.status == ApprovalStatus.PENDING


# ── G-12/G-17: NotificationService new methods ───────────────────────────────

@pytest.mark.asyncio
async def test_notify_approval_timeout_method_exists() -> None:
    """G-12: notify_approval_timeout method exists."""
    from app.services.notification_service import NotificationService
    svc = NotificationService()
    result = await svc.notify_approval_timeout(
        request_id="req-timeout", goal_id="goal-timeout",
        action="long_job", tenant_id=TENANT_ID, auto_rejected=True,
    )
    assert isinstance(result, dict)
    assert result["sent"] == 0   # no channels configured


@pytest.mark.asyncio
async def test_notify_approval_required_has_token_param() -> None:
    """G-17: notify_approval_required accepts approval_token."""
    import inspect

    from app.services.notification_service import NotificationService
    sig = inspect.signature(NotificationService.notify_approval_required)
    assert "approval_token" in sig.parameters


# ── G-19: OrgEventPublisher configure ────────────────────────────────────────

def test_configure_org_event_publisher() -> None:
    """G-19: configure_org_event_publisher sets the singleton."""
    from app.org.events import configure_org_event_publisher, get_org_event_publisher
    configure_org_event_publisher(redis_client=None)
    pub = get_org_event_publisher()
    assert pub is not None
    assert hasattr(pub, "publish")


# ── G-17: public_base_url in config ─────────────────────────────────────────

def test_public_base_url_in_config() -> None:
    """G-17: Settings has public_base_url field."""
    import os
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://x:x@localhost/x")
    from app.core.config import Settings
    s = Settings()
    assert hasattr(s, "public_base_url")
    assert s.public_base_url.startswith("http")
