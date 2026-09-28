"""SSRF: perception endpoints only checked an http(s) prefix.

Any tenant could make the server's headless browser load cloud metadata or an
internal service and receive a screenshot / its text.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.perception import router as perception_router
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="perc-ssrf", plan=PlanTier.FREE, api_key_id="k")
_H = {"X-API-Key": "perc-ssrf-key"}
_INTERNAL = "http://169.254.169.254/latest/meta-data/"


def _client() -> tuple[TestClient, MagicMock]:
    async def _resolve(key: str) -> Any:
        return _CTX if key == "perc-ssrf-key" else None

    app = FastAPI()
    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(perception_router)
    agent = MagicMock()
    agent.take_screenshot = AsyncMock()
    agent.extract_text = AsyncMock()
    app.state.browser_agent = agent
    app.state.page_analyzer = MagicMock(analyze_multiple=AsyncMock(return_value=[]))
    app.state.goal_service = MagicMock(submit_goal=AsyncMock(return_value={}))
    return TestClient(app), agent


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/perception/screenshot", {"url": _INTERNAL}),
        ("/perception/analyze", {"url": "http://127.0.0.1:6379/"}),
        ("/perception/extract", {"url": "http://10.0.0.7/admin"}),
        ("/perception/batch-analyze", {"urls": ["https://example.com", _INTERNAL]}),
        ("/perception/goal-with-image", {"goal": "g", "image_url": "http://localhost:8000/"}),
    ],
)
def test_internal_urls_are_rejected_before_the_browser_runs(path: str, body: dict) -> None:
    client, agent = _client()
    r = client.post(path, json=body, headers=_H)
    assert r.status_code == 400
    agent.take_screenshot.assert_not_called()
    agent.extract_text.assert_not_called()


async def test_browser_agent_refuses_internal_url_directly() -> None:
    from app.perception import browser_agent as mod

    agent = mod.BrowserAgent()
    mod_available = mod._PLAYWRIGHT_AVAILABLE
    try:
        mod._PLAYWRIGHT_AVAILABLE = True
        res = await agent.take_screenshot(_INTERNAL)
    finally:
        mod._PLAYWRIGHT_AVAILABLE = mod_available
    assert res.success is False
    assert "SSRF" in res.error


async def test_browser_route_guard_aborts_redirect_to_internal_host() -> None:
    from app.perception.browser_agent import _guard_route

    route = MagicMock()
    route.request.url = "http://169.254.169.254/latest"
    route.abort = AsyncMock()
    route.continue_ = AsyncMock()
    await _guard_route(route)
    route.abort.assert_awaited_once()
    route.continue_.assert_not_called()
